"""発見役の出力を緩いモデルで検証して model_dump した辞書が、元の辞書と等しいこと。

下流（prepare_candidates など）は検証前の辞書と同じ形を前提にする。
MISSING の欄のキー、整数値の形、既定値の欄の有無が変わると振る舞いが変わる。
"""

from typing import Any

import pytest
from pydantic import BaseModel

from jigane.skills import macro, micro, security

SHARDED_ABSENT = {"nit_reason", "deepens", "out_of_scope", "triage_reason"}


def _place(path: str = "a.py") -> dict[str, Any]:
    return {"path": path, "start_line": 3, "end_line": 5}


def _finding(viewpoint: str, **extra: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "severity": "warning",
        "viewpoint": viewpoint,
        "claim": "主張",
        "body": "本文",
        "evidence": [_place()],
        "declaration_ref": None,
        "judgment_region": None,
        "references": ["docs/x.md"],
        "in_scope": True,
        "scope_reason": "範囲の内",
    }
    return {**base, **extra}


def _variants(viewpoint: str) -> list[dict[str, Any]]:
    return [
        _finding(viewpoint),
        _finding(viewpoint, declaration_ref=_place("/abs/b.py"), judgment_region="節"),
        _finding(viewpoint, from_concern="F1", deepens="F2", triage_reason="理由"),
        _finding(
            viewpoint,
            severity="nit",
            nit_reason={
                "kind": "disclosed_limitation",
                "text": "文",
                "concern": None,
                "places": ["docs/x.md"],
            },
        ),
        _finding(viewpoint, out_of_scope={"paths": ["c.py"], "reason": "理由"}),
        _finding(viewpoint, evidence=[_place(), _place("d.py")], references=["a", "b"]),
    ]


def _roundtrip(model: type[BaseModel], output: dict[str, Any]) -> dict[str, Any]:
    return model.model_validate(output).model_dump(mode="json")


def test_macro_conclude_model_roundtrip() -> None:
    output: dict[str, Any] = {
        "findings": _variants("M1"),
        "merged": [{"finding_index": 0, "sources": ["S1", "S2"]}],
        "discarded": [
            {"source": "S3", "reason": "理由"},
            {
                "source": "S4",
                "reason": "理由",
                "kind": "deepening",
                "concern": "懸念",
                "disclosed_at": "docs/x.md",
            },
        ],
    }
    assert _roundtrip(macro.lenient_conclude_model(), output) == output


@pytest.mark.parametrize(
    ("model", "viewpoint"),
    [(micro.lenient_discovery_model(), "D1"), (security.lenient_discovery_model(), "S-AUTH")],
)
def test_sharded_discovery_model_roundtrip(model: type[BaseModel], viewpoint: str) -> None:
    findings = [
        {key: value for key, value in finding.items() if key not in SHARDED_ABSENT}
        for finding in _variants(viewpoint)
    ]
    findings.append(_finding(viewpoint, disclosed_at="docs/x.md", severity="nit"))
    output = {"findings": findings}
    assert _roundtrip(model, output) == output


@pytest.mark.parametrize(
    ("model", "viewpoint", "extra"),
    [
        (macro.lenient_conclude_model(), "M1", {"merged": [], "discarded": []}),
        (micro.lenient_discovery_model(), "D1", {}),
        (security.lenient_discovery_model(), "S-AUTH", {}),
    ],
)
def test_whole_float_line_dumps_as_int(
    model: type[BaseModel], viewpoint: str, extra: dict[str, Any]
) -> None:
    """位置の行が 1.0 のような整数値の浮動小数なら、dump は整数にする。

    検証の前は 1.0 のまま下流へ渡していた。この差は認めた。
    """
    place = {"path": "a.py", "start_line": 1.0, "end_line": 2.0}
    output = {"findings": [_finding(viewpoint, evidence=[place])], **extra}
    evidence = _roundtrip(model, output)["findings"][0]["evidence"]
    assert evidence == [{"path": "a.py", "start_line": 1, "end_line": 2}]
    assert type(evidence[0]["start_line"]) is int
    assert type(evidence[0]["end_line"]) is int
