"""群ごとの claude に渡すスキーマが、旧の JSON スキーマと同じ入力を受け付けることを縛る。"""

import copy
from collections.abc import Callable
from typing import Any

import jsonschema
import pytest
from legacy_schemas import frozen_schema

from jigane.llm_runs.claude import _schema_for_cli
from jigane.skills import macro, micro, security


def _output() -> dict[str, Any]:
    finding = {
        "severity": "warning",
        "viewpoint": "M1",
        "claim": "c",
        "body": "b",
        "evidence": [{"path": "src/a.py", "start_line": 1, "end_line": 2}],
        "declaration_ref": None,
        "judgment_region": None,
        "references": ["ref"],
        "in_scope": True,
        "scope_reason": "r",
    }
    return {"findings": [finding], "merged": [], "discarded": []}


_MUTATIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "valid": lambda _o: None,
    "unknown viewpoint": lambda o: o["findings"][0].__setitem__("viewpoint", "M9"),
    "absolute path": lambda o: o["findings"][0]["evidence"][0].__setitem__("path", "/a.py"),
    "drive path": lambda o: o["findings"][0]["evidence"][0].__setitem__("path", "C:x.py"),
    "zero line": lambda o: o["findings"][0]["evidence"][0].__setitem__("start_line", 0),
    "extra key": lambda o: o["findings"][0].__setitem__("extra", 1),
    "missing in_scope": lambda o: o["findings"][0].pop("in_scope"),
    "missing merged": lambda o: o.pop("merged"),
    "bad severity": lambda o: o["findings"][0].__setitem__("severity", "info"),
    "merged one source": lambda o: o.__setitem__(
        "merged", [{"finding_index": 0, "sources": ["S1"]}]
    ),
    "merged two sources": lambda o: o.__setitem__(
        "merged", [{"finding_index": 0, "sources": ["S1", "S2"]}]
    ),
    "bad source id": lambda o: o.__setitem__("discarded", [{"source": "X1", "reason": "r"}]),
    "discarded ok": lambda o: o.__setitem__("discarded", [{"source": "S1", "reason": "r"}]),
    "discarded empty reason": lambda o: o.__setitem__(
        "discarded", [{"source": "S1", "reason": ""}]
    ),
    "null declaration_ref": lambda o: o["findings"][0].__setitem__("declaration_ref", None),
}


def _cases() -> list[tuple[str, dict[str, Any]]]:
    cases = []
    for name, mutate in _MUTATIONS.items():
        output = copy.deepcopy(_output())
        mutate(output)
        cases.append((name, output))
    return cases


def test_the_model_schema_is_a_valid_2020_12_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(macro.conclude_schema())


def test_the_model_schema_keeps_the_path_pattern_and_the_disclosed_at_description() -> None:
    schema = macro.conclude_schema()
    item = schema["properties"]["findings"]["items"]["properties"]
    path = item["evidence"]["items"]["properties"]["path"]
    assert path["pattern"] == r"^(?![/\\])(?![A-Za-z]:)"
    legacy = frozen_schema("macro_conclude")["properties"]["discarded"]["items"]["properties"]
    disclosed = schema["properties"]["discarded"]["items"]["properties"]["disclosed_at"]
    assert disclosed["description"] == legacy["disclosed_at"]["description"]


def test_the_model_schema_and_the_legacy_schema_agree_on_representative_inputs() -> None:
    legacy = jsonschema.Draft202012Validator(frozen_schema("macro_conclude"))
    model = jsonschema.Draft202012Validator(macro.conclude_schema())
    through_cli = jsonschema.Draft202012Validator(_schema_for_cli(macro.conclude_schema()))
    cases = _cases()
    assert len(cases) >= 15
    for name, output in cases:
        expected = legacy.is_valid(output)
        assert model.is_valid(output) == expected, name
        assert through_cli.is_valid(output) == expected, name


def test_the_cli_schema_drops_the_dollar_keys_of_the_model_schema() -> None:
    assert not {"$schema", "$id"} & set(_schema_for_cli(macro.conclude_schema()))


def test_the_model_schema_carries_no_description_from_a_docstring() -> None:
    items = macro.conclude_schema()["properties"]["discarded"]["items"]["properties"]
    assert "description" not in items["kind"]
    assert items["disclosed_at"]["description"]


def _discovery_output(viewpoint: str) -> dict[str, Any]:
    finding = _output()["findings"][0]
    finding["viewpoint"] = viewpoint
    return {"findings": [finding]}


_DISCOVERY_MUTATIONS = (
    "absolute path",
    "drive path",
    "zero line",
    "extra key",
    "missing in_scope",
    "bad severity",
    "null declaration_ref",
)


def _discovery_cases(viewpoints: list[str]) -> list[dict[str, Any]]:
    cases = [_discovery_output(viewpoint) for viewpoint in viewpoints]
    for name in _DISCOVERY_MUTATIONS:
        output = _discovery_output(viewpoints[0])
        _MUTATIONS[name](output)
        cases.append(output)
    with_disclosed_at = _discovery_output(viewpoints[0])
    with_disclosed_at["findings"][0]["disclosed_at"] = "docs/a.md"
    cases.append(with_disclosed_at)
    return cases


_DISCOVERY_GROUPS = {
    "micro": (micro, ["D1", "T4", "D5", "S-ASVS-V1", ""]),
    "security": (
        security,
        ["S-AUTH", "S-ASVS-V1", "S-ASVS-V17", "S-ASVS-V18", "S-ASVS-V0", "S-ASVS-V1\n", "D1", ""],
    ),
}


@pytest.mark.parametrize("name", list(_DISCOVERY_GROUPS))
def test_the_discovery_model_schema_is_valid_and_agrees_with_the_legacy_one(name: str) -> None:
    module, viewpoints = _DISCOVERY_GROUPS[name]
    model_schema = module.discovery_schema()
    jsonschema.Draft202012Validator.check_schema(model_schema)
    legacy = jsonschema.Draft202012Validator(frozen_schema(f"{name}_discovery"))
    model = jsonschema.Draft202012Validator(model_schema)
    through_cli = jsonschema.Draft202012Validator(_schema_for_cli(model_schema))
    cases = _discovery_cases(viewpoints)
    assert any(legacy.is_valid(case) for case in cases)
    assert not all(legacy.is_valid(case) for case in cases)
    for case in cases:
        expected = legacy.is_valid(case)
        assert model.is_valid(case) == expected, case
        assert through_cli.is_valid(case) == expected, case


def test_the_security_viewpoint_accepts_the_list_or_the_pattern() -> None:
    viewpoint = security.discovery_schema()["properties"]["findings"]["items"]["properties"][
        "viewpoint"
    ]
    assert len(viewpoint["anyOf"]) == 2
    assert viewpoint["anyOf"][1]["pattern"] == security.VIEWPOINT_PATTERN


@pytest.mark.parametrize("name", list(_DISCOVERY_GROUPS))
def test_the_discovery_model_schema_carries_no_description_from_a_docstring(name: str) -> None:
    module, _viewpoints = _DISCOVERY_GROUPS[name]
    item = module.discovery_schema()["properties"]["findings"]["items"]
    assert "description" not in item
    assert "description" not in item["properties"]["evidence"]["items"]


@pytest.mark.parametrize("name", list(_DISCOVERY_GROUPS))
def test_the_discovery_model_schema_keeps_the_legacy_disclosed_at_description(name: str) -> None:
    module, _viewpoints = _DISCOVERY_GROUPS[name]
    key = ("properties", "findings", "items", "properties", "disclosed_at")

    def disclosed_at(schema: dict[str, Any]) -> dict[str, Any]:
        node: Any = schema
        for part in key:
            node = node[part]
        found: dict[str, Any] = node
        return found

    model = disclosed_at(module.discovery_schema())
    legacy = disclosed_at(frozen_schema(f"{name}_discovery"))
    assert model["description"] == legacy["description"]
