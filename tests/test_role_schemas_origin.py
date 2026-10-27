"""検証の役の claude に渡すスキーマが、旧の JSON スキーマと同じ入力を受け付けることを縛る。"""

import copy
from collections.abc import Callable
from typing import Any

import jsonschema
import pytest
from legacy_schemas import frozen_schema
from pydantic import ValidationError

from jigane.llm_runs.claude import _schema_for_cli
from jigane.skills import adversarial
from jigane.skills.output_models import role_output_model

_LOCATION = {"path": "src/a.py", "start_line": 1, "end_line": 2}
_TOOL_CLAIM = {"relies": False, "basis": "none", "version": ""}

_SAMPLES: dict[str, dict[str, Any]] = {
    "rebuttal": {"index": 0, "challenge": "c", "checked": [_LOCATION]},
    "alternative": {"index": 0, "alternative": "a"},
    "defense": {"index": 0, "defense": "d"},
    "judge_rebuttal": {
        "index": 0,
        "recheck": [_LOCATION],
        "verdict": "kept",
        "reason": "r",
        "verified": True,
        "tool_claim": _TOOL_CLAIM,
    },
    "judge_alternative": {"index": 0, "verdict": "kept", "reason": "r"},
}

_COMMON: dict[str, Callable[[dict[str, Any]], None]] = {
    "valid": lambda _i: None,
    "negative index": lambda i: i.__setitem__("index", -1),
    "whole float index": lambda i: i.__setitem__("index", 1.0),
    "fractional index": lambda i: i.__setitem__("index", 1.5),
    "string index": lambda i: i.__setitem__("index", "1"),
    "bool index": lambda i: i.update(index=True),
    "missing index": lambda i: i.pop("index"),
    "extra key": lambda i: i.__setitem__("extra", 1),
}

_BY_ROLE: dict[str, dict[str, Callable[[dict[str, Any]], None]]] = {
    "rebuttal": {
        "missing challenge": lambda i: i.pop("challenge"),
        "missing checked": lambda i: i.pop("checked"),
        "zero line": lambda i: i["checked"][0].__setitem__("start_line", 0),
        "absolute path": lambda i: i["checked"][0].__setitem__("path", "/a.py"),
        "location extra key": lambda i: i["checked"][0].__setitem__("x", 1),
    },
    "alternative": {
        "missing alternative": lambda i: i.pop("alternative"),
        "defense in alternative": lambda i: i.__setitem__("defense", "d"),
        "non string": lambda i: i.__setitem__("alternative", 1),
    },
    "defense": {
        "missing defense": lambda i: i.pop("defense"),
        "alternative in defense": lambda i: i.__setitem__("alternative", "a"),
        "non string": lambda i: i.__setitem__("defense", None),
    },
    "judge_rebuttal": {
        "verdict out of enum": lambda i: i.__setitem__("verdict", "maybe"),
        "rejected": lambda i: i.__setitem__("verdict", "rejected"),
        "disclosed_at": lambda i: i.__setitem__("disclosed_at", "docs/a.md"),
        "empty disclosed_at": lambda i: i.__setitem__("disclosed_at", ""),
        "missing verified": lambda i: i.pop("verified"),
        "string verified": lambda i: i.__setitem__("verified", "true"),
        "missing tool_claim": lambda i: i.pop("tool_claim"),
        "tool_claim basis out of enum": lambda i: i["tool_claim"].__setitem__("basis", "x"),
        "tool_claim missing version": lambda i: i["tool_claim"].pop("version"),
        "tool_claim extra key": lambda i: i["tool_claim"].__setitem__("x", 1),
        "tool_claim as string": lambda i: i.__setitem__("tool_claim", "none"),
        "kind key": lambda i: i.__setitem__("kind", "rebuttal"),
        "missing recheck": lambda i: i.pop("recheck"),
    },
    "judge_alternative": {
        "verdict out of enum": lambda i: i.__setitem__("verdict", "maybe"),
        "weakened": lambda i: i.__setitem__("verdict", "weakened"),
        "disclosed_at": lambda i: i.__setitem__("disclosed_at", "docs/a.md"),
        "empty disclosed_at": lambda i: i.__setitem__("disclosed_at", ""),
        "number disclosed_at": lambda i: i.__setitem__("disclosed_at", 1),
        "missing reason": lambda i: i.pop("reason"),
        "kind key": lambda i: i.__setitem__("kind", "alternative"),
        "verified in alternative judge": lambda i: i.update(verified=True),
    },
}


def _cases(role: str) -> list[tuple[str, dict[str, Any]]]:
    cases = []
    for name, mutate in {**_COMMON, **_BY_ROLE[role]}.items():
        output = {"items": [copy.deepcopy(_SAMPLES[role])]}
        mutate(output["items"][0])
        cases.append((name, output))
    return cases


@pytest.mark.parametrize("role", list(_SAMPLES))
def test_the_model_role_schema_is_valid_and_agrees_with_the_legacy_one(role: str) -> None:
    model_schema = adversarial.role_schemas()[role]
    jsonschema.Draft202012Validator.check_schema(model_schema)
    legacy = jsonschema.Draft202012Validator(frozen_schema(f"role_{role}"))
    model = jsonschema.Draft202012Validator(model_schema)
    through_cli = jsonschema.Draft202012Validator(_schema_for_cli(model_schema))
    cases = _cases(role)
    assert len(cases) >= 10
    accepted = [name for name, case in cases if legacy.is_valid(case)]
    assert "valid" in accepted
    assert len(accepted) < len(cases)
    for name, case in cases:
        expected = legacy.is_valid(case)
        assert model.is_valid(case) == expected, name
        assert through_cli.is_valid(case) == expected, name


@pytest.mark.parametrize("role", list(_SAMPLES))
def test_the_role_output_model_accepts_what_the_legacy_schema_accepts(role: str) -> None:
    model = role_output_model(adversarial._ROLE_ITEMS[role])
    legacy = jsonschema.Draft202012Validator(frozen_schema(f"role_{role}"))
    mismatches = []
    for name, case in _cases(role):
        try:
            model.model_validate(case)
            accepted = True
        except ValidationError:
            accepted = False
        if accepted != legacy.is_valid(case):
            mismatches.append(name)
    assert mismatches == []


def _descriptions(node: object) -> list[str]:
    if isinstance(node, dict):
        found = [v for k, v in node.items() if k == "description" and isinstance(v, str)]
        return found + [d for value in node.values() for d in _descriptions(value)]
    if isinstance(node, list):
        return [d for value in node for d in _descriptions(value)]
    return []


@pytest.mark.parametrize("role", list(_SAMPLES))
def test_the_model_role_schema_carries_only_the_descriptions_the_legacy_one_has(
    role: str,
) -> None:
    model = _descriptions(adversarial.role_schemas()[role])
    legacy = _descriptions(frozen_schema(f"role_{role}"))
    assert model == legacy
