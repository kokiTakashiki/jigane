import inspect
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from review_fakes import build_content

from jigane.commands import consent
from jigane.findings.records import FINDINGS_FILE, load_history_checked
from jigane.groups.definitions import MACRO, MICRO
from jigane.reviews import lifecycle as review_module
from jigane.reviews.lifecycle import LocalRange, RangeOrigin, Review, load_review_at
from jigane.reviews.relations import related_reviews, series_of
from jigane.reviews.selection import Objected, Selection, save_selection
from jigane.skills import history, macro, micro, security, sharded
from jigane.skills.assembly import AssemblyInput
from jigane.storage.store import ReviewId, ReviewKind


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def save(
    project: Path,
    key: str,
    kind: ReviewKind,
    created_at: str,
    range_text: str = "main..feature",
    owner: str = "local",
) -> Review:
    content = build_content(project, kind)
    if kind is ReviewKind.RANGE:
        assert isinstance(content.origin, RangeOrigin)
        content = replace(
            content, origin=RangeOrigin(LocalRange(range_text, str(project)), content.origin.diff)
        )
    review_id = ReviewId(owner, "shop", key, kind)
    saved, _ = review_module.save_review(review_id, content, created_at, None, str(project))
    return saved


def finding(number: int, claim: str) -> dict[str, Any]:
    return {
        "id": f"F{number}",
        "viewpoint": "M2",
        "severity": "warning",
        "triage": "must_read",
        "claim": claim,
        "evidence": [{"path": "src/a.py", "start_line": 1, "end_line": 2}],
    }


def write_findings(review: Review, findings: list[dict[str, Any]]) -> None:
    state = {"last_number": 9, "groups": {MACRO: findings}}
    (review.id.directory() / FINDINGS_FILE).write_text(json.dumps(state), encoding="utf-8")


def keys(review: Review) -> list[str]:
    return [other.id.key for other in related_reviews(review)]


def test_series_drops_trailing_digits() -> None:
    assert series_of("equiv3") == "equiv"
    assert series_of("equiv") == "equiv"
    assert series_of("v2x") == "v2x"
    assert series_of("2026") == "2026"


def test_series_drops_hyphenated_suffix_so_first_round_joins_later_ones() -> None:
    assert series_of("name") == series_of("name-2") == series_of("name-3") == "name"
    assert series_of("name2") == "name"
    assert series_of("index-types-typekey-2") == "index-types-typekey"


def test_first_round_is_related_to_hyphenated_later_rounds(project: Path) -> None:
    save(project, "name", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    save(project, "name-2", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "c..d")
    current = save(project, "name-3", ReviewKind.RANGE, "2026-01-03T00:00:00+00:00", "e..f")
    assert keys(current) == ["name-2", "name"]


def test_same_series_range_reviews_are_related_newest_first(project: Path) -> None:
    save(project, "equiv1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    save(project, "equiv2", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "c..d")
    save(project, "other", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "e..f")
    current = save(project, "equiv3", ReviewKind.RANGE, "2026-01-03T00:00:00+00:00", "g..h")
    assert keys(current) == ["equiv2", "equiv1"]


def test_same_head_branch_is_related_without_the_series(project: Path) -> None:
    save(project, "first", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "main..feature")
    save(project, "second", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "dev..other")
    current = save(project, "third", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "x..feature")
    assert keys(current) == ["first"]


def test_feature_reviews_relate_by_series_only(project: Path) -> None:
    save(project, "self1", ReviewKind.FEATURE, "2026-01-01T00:00:00+00:00")
    save(project, "unrelated", ReviewKind.FEATURE, "2026-01-01T00:00:00+00:00")
    current = save(project, "self2", ReviewKind.FEATURE, "2026-01-02T00:00:00+00:00")
    assert keys(current) == ["self1"]


def test_other_kind_and_later_reviews_are_left_out(project: Path) -> None:
    save(project, "equiv1", ReviewKind.FEATURE, "2026-01-01T00:00:00+00:00")
    save(project, "equiv9", ReviewKind.RANGE, "2026-01-05T00:00:00+00:00")
    current = save(project, "equiv3", ReviewKind.RANGE, "2026-01-03T00:00:00+00:00")
    assert keys(current) == []


def test_other_owner_is_left_out(project: Path) -> None:
    save(project, "equiv1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", owner="someone")
    current = save(project, "equiv2", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00")
    assert keys(current) == []


def test_pull_requests_are_never_related(project: Path) -> None:
    save(project, "6", ReviewKind.PULL_REQUEST, "2026-01-01T00:00:00+00:00")
    current = save(project, "7", ReviewKind.PULL_REQUEST, "2026-01-02T00:00:00+00:00")
    assert keys(current) == []


def test_related_findings_appear_in_history_with_the_review_name(project: Path) -> None:
    older = save(project, "equiv1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    write_findings(older, [finding(2, "old claim")])
    current = save(project, "equiv2", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "c..d")
    write_findings(current, [finding(5, "own claim")])
    assert macro.history_text(AssemblyInput.load(current)).splitlines() == [
        "- 前の回 F5 M2 warning in_scope=true 区分=必読 own claim（src/a.py:1）",
        "- @equiv1 前の回 F2 M2 warning in_scope=true 区分=必読 old claim（src/a.py:1）",
    ]
    assert macro.history_text(AssemblyInput.load(current)) in macro.build_diagram_prompt(
        AssemblyInput.load(current)
    )


def test_objections_are_appended_for_own_and_related_findings(project: Path) -> None:
    older = save(project, "equiv1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    write_findings(older, [finding(2, "old claim")])
    save_selection(older.id.directory(), Selection({"F2": Objected("決着済み\nなので")}))
    current = save(project, "equiv2", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "c..d")
    write_findings(current, [finding(5, "own claim"), finding(6, "plain")])
    save_selection(current.id.directory(), Selection({"F5": Objected("誤り")}))
    assert macro.history_text(
        AssemblyInput.load(load_review_at(current.id.directory()))
    ).splitlines() == [
        (
            "- 前の回 F5 M2 warning in_scope=true 区分=必読 own claim（src/a.py:1）"
            "（利用者の異議: 誤り）"
        ),
        "- 前の回 F6 M2 warning in_scope=true 区分=必読 plain（src/a.py:1）",
        (
            "- @equiv1 前の回 F2 M2 warning in_scope=true 区分=必読 old claim（src/a.py:1）"
            "（利用者の異議: 決着済み なので）"
        ),
    ]


def test_related_findings_alone_replace_the_empty_history_text(project: Path) -> None:
    older = save(project, "equiv1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    write_findings(older, [finding(2, "old claim")])
    current = save(project, "equiv2", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "c..d")
    expected = "- @equiv1 前の回 F2 M2 warning in_scope=true 区分=必読 old claim（src/a.py:1）"
    assert macro.history_text(AssemblyInput.load(current)) == expected


def test_history_lines_carry_the_previous_weight(project: Path) -> None:
    current = save(project, "solo", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "c..d")
    outside = {**finding(2, "outside"), "severity": "critical"}
    outside["out_of_scope"] = {"paths": [], "reason": "範囲の外を直す"}
    write_findings(current, [finding(1, "inside"), outside])
    assert macro.history_text(AssemblyInput.load(current)).splitlines() == [
        "- 前の回 F1 M2 warning in_scope=true 区分=必読 inside（src/a.py:1）",
        "- 前の回 F2 M2 critical in_scope=false outside（src/a.py:1）",
    ]


def test_history_is_unchanged_without_related_reviews(project: Path) -> None:
    current = save(project, "solo", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "c..d")
    assert macro.history_text(AssemblyInput.load(current)) == macro.NO_HISTORY_TEXT
    write_findings(current, [finding(1, "own claim")])
    expected = "- 前の回 F1 M2 warning in_scope=true 区分=必読 own claim（src/a.py:1）"
    assert macro.history_text(AssemblyInput.load(current)) == expected


def micro_finding(number: int, path: str, triage: str, verdict: str) -> dict[str, Any]:
    return {
        "id": f"F{number}",
        "viewpoint": "D1",
        "severity": "warning",
        "claim": f"claim {number}",
        "evidence": [{"path": path, "start_line": 3, "end_line": 4}],
        "triage": triage,
        "adjudication": {
            "kind": "rebuttal",
            "challenge": "c",
            "recheck": [],
            "reason": "r",
            "verdict": verdict,
        },
    }


def test_micro_history_carries_triage_and_verdict_for_the_shard_files_only(project: Path) -> None:
    older = save(project, "retry", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    state = {
        "last_number": 9,
        "groups": {
            "micro": [
                micro_finding(1, "src/a.py", "must_read", "weakened"),
                micro_finding(2, "src/b.py", "remaining", "kept"),
            ]
        },
    }
    (older.id.directory() / FINDINGS_FILE).write_text(json.dumps(state), encoding="utf-8")
    current = save(project, "retry-2", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "c..d")
    assert history.history_text(AssemblyInput.load(current), MICRO, ["src/a.py"]).splitlines() == [
        "- @retry 前の回 F1 D1 warning 区分=必読 反証=weakened claim 1（src/a.py:3）",
    ]
    assert (
        history.history_text(AssemblyInput.load(current), MICRO, ["src/c.py"])
        == history.NO_HISTORY_TEXT
    )
    # 方針の群の履歴は実装の資料に混ざらない
    assert history.history_text(AssemblyInput.load(current), MACRO) == history.NO_HISTORY_TEXT


def test_macro_lines_carry_triage_and_deepens_for_own_and_related_rounds(project: Path) -> None:
    older = save(project, "equiv1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    write_findings(older, [{**finding(1, "old deep"), "deepens": "F3", "triage": "remaining"}])
    current = save(project, "equiv2", ReviewKind.RANGE, "2026-01-02T00:00:00+00:00", "c..d")
    write_findings(current, [{**finding(5, "own deep"), "deepens": "F2", "triage": "remaining"}])
    assert macro.history_text(AssemblyInput.load(current)).splitlines() == [
        (
            "- 前の回 F5 M2 warning in_scope=true 区分=残り deepens=F2 深掘り（必読に数えない） "
            "own deep（src/a.py:1）"
        ),
        (
            "- @equiv1 前の回 F1 M2 warning in_scope=true 区分=残り deepens=F3 "
            "深掘り（必読に数えない） old deep（src/a.py:1）"
        ),
    ]


def test_micro_lines_ignore_deepens(project: Path) -> None:
    review = save(project, "equiv1", ReviewKind.RANGE, "2026-01-01T00:00:00+00:00", "a..b")
    state = {
        "last_number": 9,
        "groups": {"micro": [{**finding(1, "c"), "deepens": "F3", "triage": "remaining"}]},
    }
    (review.id.directory() / FINDINGS_FILE).write_text(json.dumps(state), encoding="utf-8")
    assert history.history_text(AssemblyInput.load(review), MICRO) == (
        "- 前の回 F1 M2 warning 区分=残り 反証=なし c（src/a.py:1）"
    )


def test_the_history_input_is_required_by_every_material_builder() -> None:
    for function in (
        macro.history_text,
        macro.diagram_materials_text,
        macro.build_diagram_prompt,
        macro.diagram_plan,
        macro.design_plan,
        macro.macro_plan,
        macro.run_macro,
        macro.estimate_macro,
        consent.launch_plan,
    ):
        parameter = inspect.signature(function).parameters["assembly"]
        assert parameter.default is inspect.Parameter.empty, function.__qualname__
    for taker in (
        history.history_text,
        sharded.build_shard_prompt,
        sharded.run_sharded,
        micro.run_micro,
        micro.estimate_micro,
        security.run_security,
        security.estimate_security,
    ):
        parameters = inspect.signature(taker).parameters
        parameter = parameters["assembly"]
        assert parameter.default is inspect.Parameter.empty, taker.__qualname__
        # review と履歴を別々の引数で並べない。AssemblyInput の一つの値で受ける
        assert "source" not in parameters, taker.__qualname__
        assert "review" not in parameters, taker.__qualname__


def test_from_loaded_reads_only_the_related_reviews(project: Path) -> None:
    review = save(project, "p", ReviewKind.RANGE, "2026-01-01T00:00:00")
    own = load_history_checked(review.id.directory())
    assert AssemblyInput.from_loaded(review, own).source.own is own
