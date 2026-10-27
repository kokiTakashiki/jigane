import ast
import itertools
import json
from pathlib import Path

import pytest
from review_fakes import stored_finding
from triage_support import assigned, decided

import jigane
from jigane.findings.records import FINDINGS_FILE as _STORE_FILE
from jigane.findings.records import (
    MUST_READ_THEMES,
    FilledFindings,
    OutOfScopeDecision,
    Placement,
    Triage,
    TriagedFinding,
    count_by_triage,
    count_heading,
    fill_triage,
    group_by_triage,
    load_findings_state,
    triage_of,
    triage_sort_key,
    triaged_dicts,
)
from jigane.groups.definitions import GROUP_IDS, MACRO, MICRO, SECURITY, GroupId

SOURCE_FILE = "findings/records.py"


def test_declaration_order_is_the_display_order() -> None:
    assert [t.value for t in Triage] == [
        "must_read",
        "unverified",
        "remaining",
        "folded",
        "out_of_scope",
        "rejected",
    ]
    assert [t.traits.label for t in Triage] == [
        "必読",
        "未検証",
        "残り",
        "折りたたみ",
        "範囲外",
        "棄却",
    ]


def test_only_must_read_blocks_the_gate() -> None:
    assert [t for t in Triage if t.traits.blocks_gate] == [Triage.MUST_READ]


def test_only_must_read_is_read_first() -> None:
    # 関門を塞ぐこととは別の性質。いまは同じ区分を指すが、片方だけ変わりうる
    assert [t for t in Triage if t.traits.read_first] == [Triage.MUST_READ]


def test_display_properties() -> None:
    assert {t: t.traits.placement for t in Triage} == {
        Triage.MUST_READ: Placement.IN_ORDER,
        Triage.UNVERIFIED: Placement.IN_ORDER,
        Triage.REMAINING: Placement.IN_ORDER,
        Triage.FOLDED: Placement.COLLAPSED,
        Triage.OUT_OF_SCOPE: Placement.SCOPE_SECTION,
        Triage.REJECTED: Placement.DISMISSED_SECTION,
    }


def test_unknown_triage_is_read_as_none_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    finding = {"id": "F1", "triage": "bogus", "severity": "warning", "adjudication": None}
    state = {
        "last_number": 1,
        "groups": {"macro": [finding]},
        "themes": [{"id": "T1", "triage": "bogus2"}],
    }
    (tmp_path / _STORE_FILE).write_text(json.dumps(state), encoding="utf-8")
    # 標準エラーへ出るのは CLI の入口が handler を付けた後だけなので、logging の記録で受ける
    with caplog.at_level("WARNING", logger="jigane.findings.records"):
        loaded = load_findings_state(tmp_path)
    # 知らない名前の指摘は、区分の無い指摘と同じく実行時の規則で補う
    assert loaded.groups[GroupId("macro")][0].decision.triage is Triage.MUST_READ
    assert loaded.themes[0].triage is None
    messages = [record.getMessage() for record in caplog.records]
    assert any("'bogus'" in message for message in messages)
    assert any("'bogus2'" in message for message in messages)


def test_groups_per_triage() -> None:
    assert Triage.FOLDED.traits.groups == (MICRO,)
    for triage in (Triage.MUST_READ, Triage.REMAINING, Triage.REJECTED, Triage.OUT_OF_SCOPE):
        assert set(triage.traits.groups) == {MACRO, MICRO, SECURITY}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("must_read", Triage.MUST_READ),
        ("deferred", Triage.REMAINING),
        ("out_of_scope", Triage.OUT_OF_SCOPE),
        ("unknown", None),
        ("", None),
        (None, None),
    ],
)
def test_triage_of(value: object, expected: Triage | None) -> None:
    assert triage_of(value) == expected


def _literals(tree: ast.AST, names: set[str]) -> list[ast.Constant]:
    # out_of_scope は区分の名前であり、判定の記録を入れる指摘のキーでもある。キーの参照は除く
    record_keys = {
        id(node.slice)
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == "out_of_scope"
    }
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in names and id(node) not in record_keys
    ]


# 区分と別の語彙が、たまたま同じ綴りを持つファイル。「相対パス: 理由」で足す
ALLOWED_LITERAL_FILES: dict[str, str] = {
    "flaky/catalog.py": "EquivalentState.UNVERIFIED は同値の確かめの状態で、指摘の区分ではない",
}


def test_triage_names_do_not_appear_as_literals_outside_findings() -> None:
    names = {t.value for t in Triage}
    root = Path(jigane.__file__).parent
    found = [
        f"{path.relative_to(root).as_posix()}:{node.lineno}: {node.value!r}"
        for path in sorted(root.rglob("*.py"))
        if path.relative_to(root).as_posix() not in {SOURCE_FILE, *ALLOWED_LITERAL_FILES}
        for node in _literals(ast.parse(path.read_text(encoding="utf-8")), names)
    ]
    assert found == []


# 区分の名前との比較は findings/records.py の外に置かない。表示の違いは Triage の性質を引く。
# 付与の規則として残す比較は、ここに「相対パス: 理由」で足す。無ければ空。
ALLOWED_COMPARISONS: dict[str, str] = {}


def _is_member(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Triage"
    )


def _comparisons(tree: ast.AST) -> list[ast.Compare]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(
            _is_member(side)
            or (
                isinstance(side, ast.Tuple | ast.List | ast.Set)
                and any(map(_is_member, side.elts))
            )
            for side in [node.left, *node.comparators]
        )
    ]


def test_triage_members_are_not_compared_outside_findings() -> None:
    root = Path(jigane.__file__).parent
    found = [
        f"{rel}:{node.lineno}"
        for path in sorted(root.rglob("*.py"))
        if path.relative_to(root).as_posix() != SOURCE_FILE
        and (rel := path.relative_to(root).as_posix()) not in ALLOWED_COMPARISONS
        for node in _comparisons(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert found == []


def test_the_comparison_check_detects_each_form() -> None:
    forms = [
        "a == Triage.X",
        "a != Triage.X",
        "a is Triage.X",
        "a in (Triage.X, Triage.Y)",
        "Triage.X == a",
    ]
    for form in forms:
        assert _comparisons(ast.parse(form)), form
    assert _comparisons(ast.parse("a.dismissed == b")) == []


def _match_patterns(tree: ast.AST) -> list[ast.MatchValue]:
    # `case Triage.X:` は比較と同じ振る舞いなので、比較と同じく findings/records.py の外に置かない
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.MatchValue) and _is_member(node.value)
    ]


def test_triage_members_are_not_matched_outside_findings() -> None:
    root = Path(jigane.__file__).parent
    found = [
        f"{rel}:{node.lineno}"
        for path in sorted(root.rglob("*.py"))
        if path.relative_to(root).as_posix() != SOURCE_FILE
        and (rel := path.relative_to(root).as_posix()) not in ALLOWED_COMPARISONS
        for node in _match_patterns(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert found == []


def test_the_match_check_detects_value_patterns_including_or() -> None:
    source = (
        "match a:\n    case Triage.X | Triage.Y:\n        pass\n    case Triage.Z:\n        pass\n"
    )
    assert len(_match_patterns(ast.parse(source))) == 3
    assert _match_patterns(ast.parse("match a:\n    case 1:\n        pass\n")) == []


def _typed(number: int, triage: Triage) -> TriagedFinding:
    stored = stored_finding(
        {"id": f"F{number}", "triage": triage, "severity": "warning", "adjudication": None}
    )
    return fill_triage(MACRO, stored)


def test_item_helpers_read_typed_findings_through_filled_triage() -> None:
    items = [_typed(1, Triage.REJECTED), _typed(2, Triage.REMAINING), _typed(4, Triage.MUST_READ)]
    assert [triage_sort_key(i) for i in items] == [5, 2, 0]
    assert [(t, [i.finding.id for i in m]) for t, m in group_by_triage(items)] == [
        (Triage.MUST_READ, ["F4"]),
        (Triage.REMAINING, ["F2"]),
        (Triage.REJECTED, ["F1"]),
    ]
    assert count_by_triage(items) == {
        Triage.MUST_READ: 1,
        Triage.UNVERIFIED: 0,
        Triage.REMAINING: 1,
        Triage.FOLDED: 0,
        Triage.OUT_OF_SCOPE: 0,
        Triage.REJECTED: 1,
    }


def test_only_rejected_is_left_out_of_the_finding_count() -> None:
    assert [t for t in Triage if not t.traits.counted_as_finding] == [Triage.REJECTED]
    items = [_typed(1, Triage.MUST_READ), _typed(2, Triage.REJECTED), _typed(3, Triage.REJECTED)]
    assert count_heading("方針の指摘", items) == "方針の指摘: 1 件（棄却 2 件）"


def _finding(number: int, **extra: object) -> dict[str, object]:
    return {
        "id": f"F{number}",
        "severity": "warning",
        "adjudication": None,
        "theme": "B1",
        **extra,
    }


def _load(
    tmp_path: Path, findings: list[dict[str, object]], theme: dict[str, object]
) -> FilledFindings:
    state = {"last_number": 2, "groups": {"micro": findings}, "themes": [theme]}
    (tmp_path / _STORE_FILE).write_text(json.dumps(state), encoding="utf-8")
    return load_findings_state(tmp_path)


def test_theme_triage_is_derived_from_its_findings_not_the_stored_copy(tmp_path: Path) -> None:
    loaded = _load(
        tmp_path,
        [_finding(1, triage="remaining"), _finding(2, triage="remaining")],
        {"id": "B1", "finding_ids": ["F1", "F2"], "triage": "must_read"},
    )
    assert loaded.themes[0].triage is Triage.REMAINING


def test_theme_only_triage_in_old_file_moves_to_findings_and_agrees(tmp_path: Path) -> None:
    loaded = _load(
        tmp_path,
        [_finding(1), _finding(2)],
        {"id": "B1", "finding_ids": ["F1", "F2"], "triage": "remaining"},
    )
    assert [f["triage"] for f in triaged_dicts(loaded.groups[GroupId("micro")])] == [
        Triage.REMAINING
    ] * 2
    assert loaded.themes[0].triage is Triage.REMAINING


def test_theme_without_readable_findings_has_no_triage(tmp_path: Path) -> None:
    loaded = _load(tmp_path, [], {"id": "B1", "finding_ids": ["F1"], "triage": "must_read"})
    assert loaded.themes[0].triage is None


def test_triaged_yields_exactly_the_triages_each_group_allows() -> None:
    reached: dict[str, set[Triage]] = {group: set() for group in GROUP_IDS}
    for group in GROUP_IDS:
        for severity, adjudication, position, scope_record, stored_triage in itertools.product(
            ("nit", "warning"),
            (
                None,
                {
                    "kind": "rebuttal",
                    "challenge": "c",
                    "recheck": [],
                    "reason": "r",
                    "verdict": "rejected",
                },
                {
                    "kind": "rebuttal",
                    "challenge": "c",
                    "recheck": [],
                    "reason": "r",
                    "verdict": "kept",
                },
            ),
            (None, 0, MUST_READ_THEMES - 1, MUST_READ_THEMES),
            (None, {"reason": "x"}),
            # 未検証は保存のときに付く区分で、重さや裁定からは導かれない
            (None, Triage.UNVERIFIED) if group in Triage.UNVERIFIED.traits.groups else (None,),
        ):
            finding = {
                "severity": severity,
                "adjudication": adjudication,
                **({} if stored_triage is None else {"triage": stored_triage}),
            }
            reached[group].add(
                assigned(group, finding, theme_position=position, scope_record=scope_record)
            )
    declared = {
        group: {triage for triage in Triage if group in triage.traits.groups}
        for group in GROUP_IDS
    }
    assert reached == declared


def test_decision_pairs_the_scope_record_with_the_out_of_scope_triage_only() -> None:
    record = {"paths": ["a.py"], "reason": "x"}
    for group in GROUP_IDS:
        for severity, adjudication, position, scope_record in itertools.product(
            ("nit", "warning"),
            (
                None,
                {
                    "kind": "rebuttal",
                    "challenge": "c",
                    "recheck": [],
                    "reason": "r",
                    "verdict": "rejected",
                },
            ),
            (None, 0, MUST_READ_THEMES),
            (None, record),
        ):
            finding = {"severity": severity, "adjudication": adjudication}
            decision = decided(group, finding, theme_position=position, scope_record=scope_record)
            # 範囲外になるのは、記録があり、棄却されていないときだけ
            expect_scope = scope_record is not None and adjudication is None
            assert isinstance(decision, OutOfScopeDecision) == expect_scope
            if isinstance(decision, OutOfScopeDecision):
                assert decision.out_of_scope == record


VIEWS = Path(jigane.__file__).parent / "commands" / "views" / "findings.py"


def _reads_placement(function: ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.Attribute) and node.attr == "placement" for node in ast.walk(function)
    )


def _is_placement_member(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Placement"
    )


def _calls_assert_never(function: ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "assert_never"
        for node in ast.walk(function)
    )


def test_every_cli_surface_reading_placement_matches_it_exhaustively() -> None:
    tree = ast.parse(VIEWS.read_text(encoding="utf-8"))
    readers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and _reads_placement(node)
    ]
    # 方針と安全性の面が読む。実装の面は表示の分類で分け、置き場所を読まない
    assert {f.name for f in readers} == {"macro_view_lines", "security_view_lines"}
    for function in readers:
        assert any(isinstance(n, ast.Match) for n in ast.walk(function)), function.name
        assert _calls_assert_never(function), function.name
    # 個別の比べ方（`is Placement.X`、`== Placement.X`）では置き場所を読まない
    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(_is_placement_member(side) for side in [node.left, *node.comparators])
    ]


def test_unknown_triage_warning_goes_through_logging(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    finding = {"id": "F1", "triage": "bogus", "severity": "warning", "adjudication": None}
    state = {"last_number": 1, "groups": {"macro": [finding]}}
    (tmp_path / _STORE_FILE).write_text(json.dumps(state), encoding="utf-8")
    with caplog.at_level("WARNING", logger="jigane.findings.records"):
        load_findings_state(tmp_path)
    assert any("bogus" in record.getMessage() for record in caplog.records)
