"""指摘のモデルが、旧の JSON スキーマと同じ入力を受け付けることを縛る。"""

import copy
from typing import Any

import jsonschema
import pytest
from legacy_schemas import load_finding_schema

from jigane.findings.discard import DiscardKind
from jigane.llm_runs.finding_model import Finding

_LOCATION: dict[str, Any] = {"path": "src/a.py", "start_line": 3, "end_line": 5}
_REBUTTAL: dict[str, Any] = {
    "kind": "rebuttal",
    "challenge": "c",
    "recheck": [_LOCATION],
    "verdict": "upheld",
    "reason": "r",
}
_ALTERNATIVE: dict[str, Any] = {
    "kind": "alternative",
    "alternative": "a",
    "defense": "d",
    "verdict": "upheld",
    "reason": "r",
}
_NIT_REASON: dict[str, Any] = {
    "kind": next(iter(DiscardKind)).value,
    "text": "t",
    "concern": None,
    "places": ["x"],
}
_OUT_OF_SCOPE: dict[str, Any] = {"paths": ["a"], "reason": "r"}
_TOOL_CLAIM: dict[str, Any] = {"relies": True, "basis": "documentation", "version": "1"}


def _base() -> dict[str, Any]:
    return {
        "id": "F1",
        "severity": "warning",
        "group": "macro",
        "viewpoint": "design",
        "claim": "claim",
        "body": "body",
        "evidence": [dict(_LOCATION)],
        "declaration_ref": None,
        "judgment_region": None,
        "references": [],
        "adjudication": None,
        "theme": None,
        "triage": "must_read",
    }


def _with(**changes: object) -> dict[str, Any]:
    return {**_base(), **changes}


def _without(*keys: str) -> dict[str, Any]:
    return {key: value for key, value in _base().items() if key not in keys}


def _located(**changes: object) -> dict[str, Any]:
    return {**_LOCATION, **changes}


_CASES: dict[str, dict[str, Any]] = {
    "plain": _base(),
    "rebuttal": _with(adjudication=dict(_REBUTTAL)),
    "rebuttal_with_optional": _with(
        adjudication={**_REBUTTAL, "verified": True, "tool_claim": _TOOL_CLAIM}
    ),
    "rebuttal_null_verified": _with(adjudication={**_REBUTTAL, "verified": None}),
    "alternative": _with(adjudication=dict(_ALTERNATIVE)),
    "nit_reason": _with(nit_reason=dict(_NIT_REASON)),
    "nit_reason_concern": _with(nit_reason={**_NIT_REASON, "concern": "c"}),
    "nit_reason_empty_concern": _with(nit_reason={**_NIT_REASON, "concern": ""}),
    "nit_reason_missing_concern": _with(
        nit_reason={key: value for key, value in _NIT_REASON.items() if key != "concern"}
    ),
    "nit_reason_null": _with(nit_reason=None),
    "out_of_scope": _with(triage="out_of_scope", out_of_scope=dict(_OUT_OF_SCOPE)),
    "out_of_scope_missing": _with(triage="out_of_scope"),
    "out_of_scope_without_triage": _with(out_of_scope=dict(_OUT_OF_SCOPE)),
    "out_of_scope_empty_reason": _with(out_of_scope={"paths": [], "reason": ""}),
    "discarded": _with(viewpoint="discarded", severity="nit", evidence=[]),
    "discarded_with_evidence": _with(viewpoint="discarded", severity="nit"),
    "discarded_not_nit": _with(viewpoint="discarded", evidence=[]),
    "empty_evidence": _with(evidence=[]),
    "all_optional_ids": _with(deepens="F2", from_concern="F3", triage_reason="r"),
    "bad_deepens": _with(deepens="G2"),
    "null_deepens": _with(deepens=None),
    "empty_triage_reason": _with(triage_reason=""),
    "null_triage": _with(triage=None),
    "theme_and_region": _with(theme="t", judgment_region="r"),
    "declaration_ref": _with(declaration_ref=dict(_LOCATION)),
    "missing_required": _without("claim"),
    "missing_triage": _without("triage"),
    "extra_item": _with(unknown=1),
    "extra_in_location": _with(evidence=[_located(extra=1)]),
    "wrong_type_body": _with(body=1),
    "wrong_type_references": _with(references="x"),
    "empty_reference": _with(references=[""]),
    "absolute_path": _with(evidence=[_located(path="/etc/passwd")]),
    "backslash_path": _with(evidence=[_located(path="\\a\\b")]),
    "drive_path": _with(evidence=[_located(path="C:\\a.py")]),
    "empty_path": _with(evidence=[_located(path="")]),
    "letter_colon_path": _with(evidence=[_located(path="a:b")]),
    "colon_not_second": _with(evidence=[_located(path="ab:c")]),
    "digit_then_colon": _with(evidence=[_located(path="1:x")]),
    "non_ascii_letter_colon": _with(evidence=[_located(path="ä:x")]),
    "dot_relative_path": _with(evidence=[_located(path="./x")]),
    "nested_relative_path": _with(evidence=[_located(path="x/y")]),
    "single_letter_path": _with(evidence=[_located(path="C")]),
    "id_shape": _with(id="f1"),
    "id_trailing_newline": _with(id="F1\n"),
    "id_without_number": _with(id="F"),
    "bad_severity": _with(severity="info"),
    "bad_group": _with(group="nope"),
    "bad_triage": _with(triage="nope"),
    "empty_viewpoint": _with(viewpoint=""),
    "kind_mismatch": _with(adjudication={**_REBUTTAL, "kind": "alternative"}),
    "alternative_with_rebuttal_items": _with(adjudication={**_ALTERNATIVE, "recheck": []}),
    "unknown_kind": _with(adjudication={**_REBUTTAL, "kind": "other"}),
    "bad_verdict": _with(adjudication={**_REBUTTAL, "verdict": "nope"}),
    "bad_basis": _with(adjudication={**_REBUTTAL, "tool_claim": {**_TOOL_CLAIM, "basis": "nope"}}),
    "bad_discard_kind": _with(nit_reason={**_NIT_REASON, "kind": "nope"}),
    "float_whole_line": _with(evidence=[_located(start_line=1.0)]),
    "float_fraction_line": _with(evidence=[_located(start_line=1.5)]),
    "bool_line": _with(evidence=[_located(end_line=True)]),
    "zero_line": _with(evidence=[_located(start_line=0)]),
    "string_line": _with(evidence=[_located(start_line="3")]),
    "short_declaration_ref": _with(declaration_ref={"path": "a"}),
    "null_evidence": _with(evidence=None),
    "null_body": _with(body=None),
    "null_group": _with(group=None),
}


def test_there_are_enough_cases() -> None:
    assert len(_CASES) >= 30


def test_both_accepted_and_rejected_cases_exist() -> None:
    validator = jsonschema.Draft202012Validator(load_finding_schema())
    assert {validator.is_valid(case) for case in _CASES.values()} == {True, False}


@pytest.mark.parametrize("name", sorted(_CASES))
def test_model_accepts_exactly_what_the_json_schema_accepts(name: str) -> None:
    candidate = copy.deepcopy(_CASES[name])
    expected = jsonschema.Draft202012Validator(load_finding_schema()).is_valid(candidate)
    try:
        Finding.model_validate(candidate)
        actual = True
    except ValueError:
        actual = False
    assert actual == expected
