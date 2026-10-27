"""変更した正規表現の札が、flaky の違反と同じ gates.json の経路に載ること。"""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from review_fakes import EXTRA_WARNING as EXTRA_FLAKY

from jigane.commands.views.review import gate_lines, has_gate_violation
from jigane.flaky.catalog import load_catalog
from jigane.flaky.gates import update_gates
from jigane.gates.entry import Active, Allowed, Gates, GateSeverity, GateSource
from jigane.gates.line_scope import AllLines, ChangedLines, LineScope
from jigane.judgment_map.allow_annotation import AllowedSpot, AllowScan
from jigane.judgment_map.classification import FilePair, Segment, build_judgment_map
from jigane.judgment_map.configuration import Config
from jigane.regex.gate_tags import detect_tags
from jigane.reviews.lifecycle import Review

WARNING_SOURCE = 'import re\n\nPATTERN = re.compile(r"(a+)+$")\n'
NIT_SOURCE = 'import re\n\nTEXT = re.sub("abc", "", "xabc")\n'


def _gates(tmp_path: Path, source: str, name: str = "a.py", *, with_base: bool = True) -> Gates:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / name).write_text(source, encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    # 変更前の木は空。比べる相手が空なら、head の正規表現は全部が変更として札になる
    base = tmp_path / "empty_base" if with_base else None
    if base is not None:
        base.mkdir(exist_ok=True)
    segment = cast("Segment", SimpleNamespace(new_path=f"src/{name}", old_path=None))
    return update_gates(
        out, tmp_path, [f"src/{name}"], [segment], load_catalog(), base_root=base, renames={}
    )


def _review(gates: Gates) -> Review:
    return cast("Review", SimpleNamespace(gates=gates))


def test_tags_become_violations_with_severity_and_source(tmp_path: Path) -> None:
    gates = _gates(tmp_path, WARNING_SOURCE)
    [entry] = gates.violations
    assert entry.number == "G1"
    assert entry.id == "R7"
    assert (entry.path, entry.line) == ("src/a.py", 3)
    assert entry.severity is GateSeverity.REGEX_WARNING
    assert entry.source is GateSource.REGEX
    assert entry.explanation["text"]
    assert gates.warnings == ()


def test_nit_tag_is_a_violation_entry_too(tmp_path: Path) -> None:
    [entry] = _gates(tmp_path, NIT_SOURCE).violations
    assert (entry.id, entry.severity) == ("R6", GateSeverity.REGEX_NIT)


def test_numbers_are_never_reused_for_tags(tmp_path: Path) -> None:
    assert _gates(tmp_path, WARNING_SOURCE).violations[0].number == "G1"
    assert _gates(tmp_path, "x = 1\n").violations == ()
    changed = 'import re\n\nPATTERN = re.compile(r"(b+)+$")\n'
    assert _gates(tmp_path, changed).violations[0].number == "G2"
    assert _gates(tmp_path, WARNING_SOURCE).violations[0].number == "G1"


def test_pattern_already_in_base_is_not_a_tag(tmp_path: Path) -> None:
    base = tmp_path / "base"
    (base / "src").mkdir(parents=True)
    (base / "src" / "a.py").write_text(WARNING_SOURCE, encoding="utf-8")
    head = tmp_path / "head"
    (head / "src").mkdir(parents=True)
    (head / "src" / "a.py").write_text(WARNING_SOURCE, encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    gates = update_gates(out, head, ["src/a.py"], [], load_catalog(), base_root=base, renames={})
    assert gates.violations == ()


def test_without_a_base_tree_no_tag_is_made(tmp_path: Path) -> None:
    gates = _gates(tmp_path, WARNING_SOURCE, with_base=False)
    assert gates.violations == ()


def test_nit_tags_count_for_exit_code(tmp_path: Path) -> None:
    nit = _gates(tmp_path, NIT_SOURCE)
    assert has_gate_violation(_review(nit))
    assert has_gate_violation(_review(Gates((*nit.violations, EXTRA_FLAKY))))


def test_gate_lines_count_the_nit(tmp_path: Path) -> None:
    lines = gate_lines(_review(_gates(tmp_path, NIT_SOURCE)))
    assert lines[0] == "ゲート違反 1 件、警告 0 件"
    assert "G1 R6 src/a.py:3（nit）" in lines[1]


ALLOWED_NOTE = "allow-regex R6 -- 外部の仕様の形をそのまま写すため"
ALLOWED_SOURCE = f'import re\n\n# jigane: {ALLOWED_NOTE}\nTEXT = re.sub("abc", "", "xabc")\n'


def test_annotation_before_the_line_keeps_the_tag_as_allowed(tmp_path: Path) -> None:
    gates = _gates(tmp_path, ALLOWED_SOURCE)
    [entry] = gates.violations
    assert (entry.id, entry.line) == ("R6", 4)
    assert entry.state == Allowed("外部の仕様の形をそのまま写すため")
    assert not entry.blocks_exit
    assert not has_gate_violation(_review(gates))
    lines = gate_lines(_review(gates))
    assert lines[0] == "ゲート違反 0 件、警告 0 件、外した札 1 件"
    assert "外しました（外部の仕様の形をそのまま写すため）" in lines[1]


def test_annotation_on_the_same_line_allows(tmp_path: Path) -> None:
    source = 'import re\n\nTEXT = re.sub("abc", "", "xabc")  # jigane: allow-regex R6 -- 理由\n'
    [entry] = _gates(tmp_path, source).violations
    assert entry.state == Allowed("理由")


def test_annotation_for_another_rule_does_not_allow(tmp_path: Path) -> None:
    source = ALLOWED_SOURCE.replace("allow-regex R6", "allow-regex R1")
    [entry] = _gates(tmp_path, source).violations
    assert entry.state == Active()
    assert entry.blocks_exit


def test_annotation_without_a_reason_or_with_an_unknown_rule_is_a_violation(
    tmp_path: Path,
) -> None:
    for note in ("allow-regex R6 --", "allow-regex R6", "allow-regex R99 -- 理由"):
        gates = _gates(tmp_path, ALLOWED_SOURCE.replace(ALLOWED_NOTE, note))
        assert sorted(e.id for e in gates.violations) == ["R6", "RGX-ANNOTATION"], note
        assert all(e.blocks_exit for e in gates.violations)
        annotation = next(e for e in gates.violations if e.id == "RGX-ANNOTATION")
        assert annotation.line == 3
        assert annotation.explanation["text"]


def test_annotation_untouched_by_the_change_is_not_reported(tmp_path: Path) -> None:
    bad = "import re\n# jigane: allow-regex R6 --\nX = 1\n"
    trees = []
    for name in ("base", "head"):
        tree = tmp_path / name
        (tree / "src").mkdir(parents=True)
        (tree / "src" / "a.py").write_text(bad, encoding="utf-8")
        trees.append(tree)
    out = tmp_path / "out"
    out.mkdir()
    segment = cast("Segment", SimpleNamespace(new_path="src/a.py", old_path="src/a.py"))
    gates = update_gates(
        out, trees[1], ["src/a.py"], [segment], load_catalog(), renames={}, base_root=trees[0]
    )
    assert gates.violations == ()


def _violations_with_changed(tmp_path: Path, scope: LineScope) -> list[str]:
    bad = "import re\n# jigane: allow-regex R6 --\nX = 1\n"
    for name in ("base", "head"):
        (tmp_path / name / "src").mkdir(parents=True)
        (tmp_path / name / "src" / "a.py").write_text(bad, encoding="utf-8")
    found = detect_tags(
        tmp_path / "head",
        ["src/a.py"],
        {},
        tmp_path / "base",
        scope,
    )
    return [v.excerpt for v in found.violations]


def test_detect_tags_without_changed_lines_reports_every_malformed_annotation(
    tmp_path: Path,
) -> None:
    assert _violations_with_changed(tmp_path, AllLines()) == ["# jigane: allow-regex R6 --"]


def test_detect_tags_with_changed_lines_reports_only_those_lines(tmp_path: Path) -> None:
    assert _violations_with_changed(tmp_path, ChangedLines({"src/a.py": frozenset({1})})) == []
    assert _violations_with_changed(
        tmp_path / "again", ChangedLines({"src/a.py": frozenset({2})})
    ) == ["# jigane: allow-regex R6 --"]


def test_detect_tags_with_changed_lines_treats_a_path_missing_from_them_as_unchanged(
    tmp_path: Path,
) -> None:
    source = WARNING_SOURCE + "# jigane: allow-regex R6 --\n"
    (tmp_path / "head" / "src").mkdir(parents=True)
    (tmp_path / "head" / "src" / "a.py").write_text(source, encoding="utf-8")
    (tmp_path / "base").mkdir()
    found = detect_tags(
        tmp_path / "head",
        ["src/a.py"],
        {},
        tmp_path / "base",
        ChangedLines({"src/b.py": frozenset({4})}),
    )
    assert found.violations == ()
    # 札の判定は scope に依らず、変更前に無い本文なら辞書に無いパスでも返る
    assert len(found.detections) == 1


def test_allowed_spot_is_keyed_by_rule_and_line() -> None:
    scan = AllowScan(allowed={AllowedSpot("R6", 3): "理由"})
    assert scan.allowed[AllowedSpot("R6", 3)] == "理由"
    assert AllowedSpot("R6", 4) not in scan.allowed


def _moved(tmp_path: Path, old_source: str, new_source: str) -> Gates:
    """src/old.py を src/new.py へ移した差分。中身が同じなら区間が無いので segments は空。"""
    for name, path, source in (("base", "old.py", old_source), ("head", "new.py", new_source)):
        (tmp_path / name / "src").mkdir(parents=True)
        (tmp_path / name / "src" / path).write_text(source, encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    return update_gates(
        out,
        tmp_path / "head",
        ["src/new.py"],
        [],
        load_catalog(),
        base_root=tmp_path / "base",
        renames={"src/new.py": "src/old.py"},
    )


def test_file_moved_without_changes_makes_no_tag(tmp_path: Path) -> None:
    for index, source in enumerate((WARNING_SOURCE, NIT_SOURCE)):
        assert _moved(tmp_path / str(index), source, source).violations == ()


def test_file_moved_with_a_new_pattern_tags_only_the_new_one(tmp_path: Path) -> None:
    gates = _moved(tmp_path, NIT_SOURCE, NIT_SOURCE + 'PATTERN = re.compile(r"(a+)+$")\n')
    [entry] = gates.violations
    assert (entry.id, entry.severity, entry.path) == (
        "R7",
        GateSeverity.REGEX_WARNING,
        "src/new.py",
    )


def test_added_file_with_same_pattern_as_another_file_still_makes_a_tag(tmp_path: Path) -> None:
    (tmp_path / "base" / "src").mkdir(parents=True)
    (tmp_path / "base" / "src" / "other.py").write_text(NIT_SOURCE, encoding="utf-8")
    (tmp_path / "head" / "src").mkdir(parents=True)
    (tmp_path / "head" / "src" / "new.py").write_text(NIT_SOURCE, encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    gates = update_gates(
        out,
        tmp_path / "head",
        ["src/new.py"],
        [],
        load_catalog(),
        renames={},
        base_root=tmp_path / "base",
    )
    [entry] = gates.violations
    assert (entry.id, entry.severity) == ("R6", GateSeverity.REGEX_NIT)


def test_judgment_map_records_a_move_without_a_segment(tmp_path: Path) -> None:
    for name, path in (("base", "old.py"), ("head", "new.py"), ("head", "same.py")):
        (tmp_path / name / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / name / "src" / path).write_text(NIT_SOURCE, encoding="utf-8")
    (tmp_path / "base" / "src" / "same.py").write_text(NIT_SOURCE, encoding="utf-8")
    pairs = [FilePair("src/old.py", "src/new.py"), FilePair("src/same.py", "src/same.py")]
    judgment_map = build_judgment_map(tmp_path / "base", tmp_path / "head", pairs, Config())
    assert judgment_map.segments == []
    assert judgment_map.renames == {"src/new.py": "src/old.py"}
