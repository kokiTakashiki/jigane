import copy
import json
from collections.abc import Callable, Iterable
from enum import StrEnum
from typing import Any

import pytest
from pydantic import ValidationError

from jigane.findings.discard import DiscardKind
from jigane.findings.records import ToolBasis, Triage, Verdict
from jigane.groups.definitions import GroupId
from jigane.llm_runs.finding_model import Finding, ToolClaim
from jigane.skills.output_models import json_schema_of


def _values(enum: Iterable[StrEnum]) -> list[str]:
    return [member.value for member in enum]


def test_the_model_schema_enums_match_the_python_enums() -> None:
    schema: dict[str, Any] = json.loads(json.dumps(json_schema_of(Finding)))
    properties = schema["properties"]
    assert properties["group"]["enum"] == _values(GroupId)
    assert properties["nit_reason"]["properties"]["kind"]["enum"] == _values(DiscardKind)
    assert properties["triage"]["anyOf"][0]["enum"] == _values(Triage)
    rebuttal, alternative = properties["adjudication"]["anyOf"][0]["oneOf"]
    assert rebuttal["properties"]["verdict"]["enum"] == _values(Verdict)
    assert alternative["properties"]["verdict"]["enum"] == _values(Verdict)
    assert rebuttal["properties"]["tool_claim"]["properties"]["basis"]["enum"] == _values(
        ToolBasis
    )


def _location() -> dict[str, Any]:
    # 箇所を書き換える試験があるため、毎回新しい辞書を返す
    return {"path": "src/app.py", "start_line": 3, "end_line": 5}


def _rebuttal_finding() -> dict[str, Any]:
    return {
        "id": "F1",
        "severity": "warning",
        "group": "micro",
        "viewpoint": "D1",
        "claim": "境界値で落ちる",
        "body": "本文",
        "evidence": [_location()],
        "declaration_ref": None,
        "judgment_region": None,
        "references": [],
        "adjudication": {
            "kind": "rebuttal",
            "challenge": "到達しない",
            "recheck": [_location()],
            "verdict": "kept",
            "reason": "到達する",
        },
        "theme": None,
        "triage": None,
    }


def _alternative_finding() -> dict[str, Any]:
    finding = _rebuttal_finding()
    finding["id"] = "F2"
    finding["group"] = "macro"
    finding["viewpoint"] = "M2"
    finding["declaration_ref"] = _location()
    finding["judgment_region"] = "J1"
    finding["references"] = ["MASVS-STORAGE-2"]
    finding["adjudication"] = {
        "kind": "alternative",
        "alternative": "別の方式",
        "defense": "現案の利点",
        "verdict": "weakened",
        "reason": "優位とは言えない",
    }
    finding["theme"] = "B1"
    finding["triage"] = "folded"
    return finding


def _accepts(candidate: dict[str, Any]) -> bool:
    try:
        Finding.model_validate(candidate)
    except ValidationError:
        return False
    return True


@pytest.mark.parametrize("build", [_rebuttal_finding, _alternative_finding])
def test_valid_finding_passes(build: Callable[[], dict[str, Any]]) -> None:
    assert _accepts(build())


def test_null_adjudication_is_valid_for_a_group_without_verification() -> None:
    finding = _alternative_finding()
    finding["adjudication"] = None
    assert _accepts(finding)


def test_empty_evidence_is_rejected() -> None:
    finding = _rebuttal_finding()
    finding["evidence"] = []
    assert not _accepts(finding)


def test_unknown_field_is_rejected() -> None:
    finding = _rebuttal_finding()
    finding["extra"] = 1
    assert not _accepts(finding)


def test_kind_and_fields_mismatch_is_rejected() -> None:
    finding = _rebuttal_finding()
    finding["adjudication"]["kind"] = "alternative"
    assert not _accepts(finding)
    mixed = copy.deepcopy(_alternative_finding())
    mixed["adjudication"]["challenge"] = "混在"
    assert not _accepts(mixed)


@pytest.mark.parametrize("path", ["/abs/file.py", "C:\\x\\file.py"])
def test_absolute_path_is_rejected(path: str) -> None:
    finding = _rebuttal_finding()
    finding["evidence"][0]["path"] = path
    assert not _accepts(finding)


def test_bad_id_is_rejected() -> None:
    finding = _rebuttal_finding()
    finding["id"] = "G1"
    assert not _accepts(finding)


def test_required_property_has_no_default() -> None:
    assert Finding.model_fields["references"].is_required()
    assert Finding.model_fields["evidence"].is_required()


@pytest.mark.parametrize("basis", [member.value for member in ToolBasis])
def test_tool_claim_accepts_every_tool_basis_and_rejects_others(basis: str) -> None:
    claim = {"relies": False, "basis": basis, "version": ""}
    ToolClaim.model_validate(claim)
    with pytest.raises(ValidationError):
        ToolClaim.model_validate({**claim, "basis": "guess"})


def test_finding_model_accepts_remaining() -> None:
    finding = _rebuttal_finding()
    finding["triage"] = "remaining"
    assert _accepts(finding)
