"""検証役の出力を役の型で検証したときの、検証の前との差を縛る。

例ごとに、検証の前の扱いを表の 1 欄目（before）に書く。
"""

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fakes import LOCATION, FakeInvoke, settings_for

from jigane.groups.definitions import MACRO
from jigane.llm_runs import claude
from jigane.skills import adversarial

Mutation = Callable[[dict[str, Any]], None]


def _candidate(claim: str) -> dict[str, Any]:
    return {
        "severity": "warning",
        "viewpoint": "D1",
        "claim": claim,
        "body": "b",
        "evidence": [LOCATION],
        "declaration_ref": None,
        "judgment_region": None,
        "references": [],
    }


def _verify(
    mutate: Mutation, *, role_key: str, claims: tuple[str, ...] = ("x",), item: int = 0
) -> tuple[adversarial.VerificationOutcome, FakeInvoke]:
    """role_key を持つ役の出力の item 番目の項目に mutate をかけて検証を走らせる。"""
    fake = FakeInvoke()

    def invoke(invocation: claude.Invocation) -> claude.InvocationResult:
        result = fake(invocation)
        # 偽の応答は位置などの辞書を他の試験と共有するので、変更の前に写す
        result = claude.InvocationResult(copy.deepcopy(result.structured_output), result.record)
        items = (result.structured_output or {}).get("items", [])
        if len(items) > item and role_key in items[0]:
            mutate(items[item])
        return result

    outcome = adversarial.verify(
        [_candidate(claim) for claim in claims],
        group=MACRO,
        changes="",
        working_directory=Path(),
        settings=settings_for(invoke),
    )
    return outcome, fake


def _set(key: str, value: object) -> Mutation:
    return lambda item: item.__setitem__(key, value)


def _drop(key: str) -> Mutation:
    return lambda item: item.pop(key)


def _location(**changes: object) -> Mutation:
    return lambda item: item["checked"][0].update(changes)


# (役の出力を見分ける欄, 変更)。どれも検証の前と後で扱いが変わる出力
_FAILS_AT_ENTRY: dict[str, tuple[str, str, str, Mutation]] = {
    "extra key in rebuttal": (
        "検証の前は、余計な欄を読み捨てて通っていた",
        "rebuttal",
        "challenge",
        _set("surprise", 1),
    ),
    "extra key in judge": (
        "検証の前は、余計な欄を読み捨てて通っていた",
        "judge",
        "recheck",
        _set("surprise", 1),
    ),
    "verdict out of enum": (
        "検証の前は、verdict の語彙の検査が裁定を読む段にあり、候補を落として数えていた",
        "judge",
        "recheck",
        _set("verdict", "maybe"),
    ),
    "index minus one": (
        "検証の前は、位置の外の index として読み捨て、欠けた候補として走らせ直していた",
        "rebuttal",
        "challenge",
        _set("index", -1),
    ),
    "index true": (
        "検証の前は、True を int として 1 の位置に読み、欠けた候補として走らせ直していた",
        "rebuttal",
        "challenge",
        lambda entry: entry.update(index=True),
    ),
    "judge without reason": (
        "検証の前も、必須の欄が無い分割の失敗だった（理由の文面だけが違う）",
        "judge",
        "recheck",
        _drop("reason"),
    ),
    "location start_line zero": (
        "検証の前は、その位置だけを捨てて続けていた",
        "rebuttal",
        "challenge",
        _location(start_line=0),
    ),
    "location start_line string": (
        "検証の前は、その位置だけを捨てて続けていた",
        "rebuttal",
        "challenge",
        _location(start_line="1"),
    ),
    "location absolute path": (
        "検証の前は、その位置だけを捨てて続けていた",
        "rebuttal",
        "challenge",
        _location(path="/a.py"),
    ),
    "location not a dict": (
        "検証の前は、その位置だけを捨てて続けていた",
        "rebuttal",
        "challenge",
        _set("checked", ["not a dict"]),
    ),
}


@pytest.mark.parametrize(
    ("before", "role", "role_key", "mutate"),
    list(_FAILS_AT_ENTRY.values()),
    ids=list(_FAILS_AT_ENTRY),
)
def test_an_output_the_role_model_rejects_fails_the_split_as_a_bad_output(
    before: str, role: str, role_key: str, mutate: Mutation
) -> None:
    """型に合わない出力は、候補を落とさず分割を BAD_OUTPUT で失敗にする。走らせ直さない。

    検証の前の扱いは表の before 欄にある。
    """
    assert before
    outcome, fake = _verify(mutate, role_key=role_key)
    assert outcome.failed_splits == 1
    assert outcome.unverified_candidates == 1
    assert outcome.findings == ()
    assert [(f.role, f.kind) for f in outcome.failures] == [(role, claude.FailureKind.BAD_OUTPUT)]
    assert len(fake.calls) == (1 if role == "rebuttal" else 2)


def test_a_whole_float_index_passes_as_it_is() -> None:
    """index が 1.0 の要素は、そのまま通って走らせ直さない。

    検証の前は、int でないので欠けた候補として走らせ直していた。
    """
    outcome, fake = _verify(_set("index", 1.0), role_key="challenge", claims=("x", "y"), item=1)
    assert outcome.failures == ()
    assert outcome.unverified_candidates == 0
    assert [f.candidate["claim"] for f in outcome.findings] == ["x", "y"]
    assert len(fake.calls) == 2
