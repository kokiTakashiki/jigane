"""役ごとの出力のモデルが、旧の入口のスキーマと同じ入力を受け付けることを縛る。"""

import copy
import re
from collections.abc import Callable
from typing import Any

import jsonschema
import pytest
from legacy_schemas import frozen_schema
from pydantic import BaseModel, ValidationError

from jigane.llm_runs.finding_model import RoleShape
from jigane.skills import macro, micro, security


def _old_lenient(schema: dict[str, Any]) -> dict[str, Any]:
    # 入口の検証がこれまで使っていた、path の pattern だけを外した写し
    relaxed = copy.deepcopy(schema)
    del relaxed["$defs"]["location"]["properties"]["path"]["pattern"]
    return relaxed


def _finding(viewpoint: str) -> dict[str, Any]:
    return {
        "severity": "warning",
        "viewpoint": viewpoint,
        "claim": "c",
        "body": "b",
        "evidence": [{"path": "src/a.py", "start_line": 1, "end_line": 2}],
        "declaration_ref": None,
        "judgment_region": None,
        "references": ["ref"],
        "in_scope": True,
        "scope_reason": "r",
    }


def _macro_output(viewpoint: str) -> dict[str, Any]:
    return {"findings": [_finding(viewpoint)], "merged": [], "discarded": []}


def _sharded_output(viewpoint: str) -> dict[str, Any]:
    return {"findings": [_finding(viewpoint)]}


Mutation = Callable[[dict[str, Any]], None]


def _first(output: dict[str, Any]) -> dict[str, Any]:
    finding: dict[str, Any] = output["findings"][0]
    return finding


def _set(key: str, value: object) -> Mutation:
    return lambda output: _first(output).__setitem__(key, value)


def _drop(key: str) -> Mutation:
    return lambda output: _first(output).pop(key)


def _evidence(**changes: object) -> Mutation:
    return lambda output: _first(output)["evidence"][0].update(changes)


_COMMON: dict[str, Mutation] = {
    "valid": lambda _o: None,
    "empty evidence": _set("evidence", []),
    "missing evidence": _drop("evidence"),
    "missing in_scope": _drop("in_scope"),
    "missing scope_reason": _drop("scope_reason"),
    "in_scope string": _set("in_scope", "yes"),
    "absolute path": _evidence(path="/abs/a.py"),
    "backslash path": _evidence(path="\\a.py"),
    "drive path": _evidence(path="C:\\a.py"),
    "empty path": _evidence(path=""),
    "extra key": _set("surprise", 1),
    "stored-only key id": _set("id", "F1"),
    "line 1.0": _evidence(start_line=1.0),
    "line 1.5": _evidence(start_line=1.5),
    "line 0": _evidence(start_line=0),
    "line true": _evidence(end_line=True),
    "line string": _evidence(end_line="2"),
    "declaration_ref": _set("declaration_ref", {"path": "/x.md", "start_line": 1, "end_line": 1}),
    "declaration_ref bad": _set("declaration_ref", {"path": "x.md"}),
    "severity unknown": _set("severity", "blocker"),
    "claim empty": _set("claim", ""),
    "body null": _set("body", None),
    "missing judgment_region": _drop("judgment_region"),
    "references empty": _set("references", []),
    "references with empty string": _set("references", [""]),
    "missing references": _drop("references"),
    "viewpoint empty": _set("viewpoint", ""),
    "viewpoint number": _set("viewpoint", 1),
    "findings not a list": lambda output: output.__setitem__("findings", "x"),
    "top-level extra": lambda output: output.__setitem__("extra", []),
    "missing findings": lambda output: output.pop("findings"),
    "no findings": lambda output: output.__setitem__("findings", []),
}
_DISCLOSED: dict[str, Mutation] = {
    "disclosed_at": _set("disclosed_at", "docs/a.md"),
    "disclosed_at empty": _set("disclosed_at", ""),
    "disclosed_at number": _set("disclosed_at", 1),
}
_CONCERN: dict[str, Mutation] = {
    "from_concern": _set("from_concern", "F1"),
    "from_concern bad": _set("from_concern", "x"),
    "from_concern null": _set("from_concern", None),
}
_MACRO: dict[str, Mutation] = {
    "merged ok": lambda o: o.__setitem__(
        "merged", [{"finding_index": 0, "sources": ["S1", "S2"]}]
    ),
    "merged one source": lambda o: o.__setitem__(
        "merged", [{"finding_index": 0, "sources": ["S1"]}]
    ),
    "merged index 1.0": lambda o: o.__setitem__(
        "merged", [{"finding_index": 1.0, "sources": ["S1", "S2"]}]
    ),
    "merged index negative": lambda o: o.__setitem__(
        "merged", [{"finding_index": -1, "sources": ["S1", "S2"]}]
    ),
    "merged bad source": lambda o: o.__setitem__(
        "merged", [{"finding_index": 0, "sources": ["S1", "T2"]}]
    ),
    "merged extra key": lambda o: o.__setitem__(
        "merged", [{"finding_index": 0, "sources": ["S1", "S2"], "x": 1}]
    ),
    "discarded minimal": lambda o: o.__setitem__("discarded", [{"source": "S3", "reason": "r"}]),
    "discarded full": lambda o: o.__setitem__(
        "discarded",
        [
            {
                "source": "S3",
                "reason": "r",
                "kind": "disclosed_limitation",
                "concern": "c",
                "disclosed_at": "docs/a.md",
            }
        ],
    ),
    "discarded bad kind": lambda o: o.__setitem__(
        "discarded", [{"source": "S3", "reason": "r", "kind": "bogus"}]
    ),
    "discarded empty reason": lambda o: o.__setitem__(
        "discarded", [{"source": "S3", "reason": ""}]
    ),
    "discarded missing reason": lambda o: o.__setitem__("discarded", [{"source": "S3"}]),
    "discarded bad source": lambda o: o.__setitem__("discarded", [{"source": "3", "reason": "r"}]),
    "discarded empty concern": lambda o: o.__setitem__(
        "discarded", [{"source": "S3", "reason": "r", "concern": ""}]
    ),
    "missing merged": lambda o: o.pop("merged"),
    "missing discarded": lambda o: o.pop("discarded"),
    "merged null": lambda o: o.__setitem__("merged", None),
}

_VIEWPOINTS = {
    "macro": ["M0", "M6", "M7", "m0", ""],
    "micro": ["D1", "T4", "D5", "S-ASVS-V1"],
    "security": [
        "S-STORAGE",
        "S-PRIVACY",
        "S-ASVS-V1",
        "S-ASVS-V9",
        "S-ASVS-V10",
        "S-ASVS-V17",
        "S-ASVS-V18",
        "S-ASVS-V0",
        "S-ASVS-V1\n",
        "X-S-ASVS-V1",
        "D1",
    ],
}


def _cases(role: str) -> list[tuple[str, dict[str, Any]]]:
    make = _macro_output if role == "macro" else _sharded_output
    base_viewpoint = {"macro": "M1", "micro": "D1", "security": "S-AUTH"}[role]
    mutations = dict(_COMMON)
    mutations.update(_CONCERN)
    if role == "macro":
        mutations.update(_MACRO)
    else:
        mutations.update(_DISCLOSED)
    cases = []
    for name, mutate in mutations.items():
        output = make(base_viewpoint)
        mutate(output)
        cases.append((name, output))
    cases.extend((f"viewpoint {viewpoint!r}", make(viewpoint)) for viewpoint in _VIEWPOINTS[role])
    return cases


def _old_schema(role: str) -> dict[str, Any]:
    if role == "macro":
        return _old_lenient(frozen_schema("macro_conclude"))
    return _old_lenient(frozen_schema(f"{role}_discovery"))


def _model(role: str) -> type[BaseModel]:
    if role == "macro":
        return macro.lenient_conclude_model()
    if role == "micro":
        return micro.lenient_discovery_model()
    return security.lenient_discovery_model()


def _accepted_by_model(model: type[BaseModel], output: dict[str, Any]) -> bool:
    try:
        model.model_validate(output)
    except ValidationError:
        return False
    return True


@pytest.mark.parametrize("role", ["macro", "micro", "security"])
def test_role_models_accept_what_the_old_entry_schema_accepts(role: str) -> None:
    validator = jsonschema.Draft202012Validator(_old_schema(role))
    model = _model(role)
    cases = _cases(role)
    assert len(cases) >= 20
    mismatches = [
        name
        for name, output in cases
        if validator.is_valid(output) != _accepted_by_model(model, output)
    ]
    assert mismatches == []


@pytest.mark.parametrize("role", ["macro", "micro", "security"])
def test_the_cases_cover_both_accepted_and_rejected_inputs(role: str) -> None:
    validator = jsonschema.Draft202012Validator(_old_schema(role))
    verdicts = {validator.is_valid(output) for _name, output in _cases(role)}
    assert verdicts == {True, False}


def test_a_role_model_is_built_once_per_arguments() -> None:
    assert micro.lenient_discovery_model() is micro.lenient_discovery_model()
    assert micro.lenient_discovery_model() is not security.lenient_discovery_model()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"viewpoint_pattern": "^X"}, "viewpoint_pattern は viewpoints と一緒に渡す"),
        (
            {"viewpoints": ("M1",), "model_written": ("claim",)},
            "viewpoints は model_written に viewpoint が要る",
        ),
        (
            {"require_references": True, "model_written": ("claim",)},
            "require_references は model_written に references が要る",
        ),
    ],
)
def test_a_role_shape_rejects_a_setting_that_would_be_ignored(
    arguments: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        RoleShape(**arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"viewpoints": ("M1",), "viewpoint_pattern": "^X"},
        {"viewpoints": ("M1",), "model_written": ("viewpoint",)},
        {"require_references": True, "model_written": ("references",)},
        {"require_references": True},
    ],
)
def test_a_role_shape_accepts_a_setting_that_takes_effect(arguments: dict[str, Any]) -> None:
    RoleShape(**arguments)
