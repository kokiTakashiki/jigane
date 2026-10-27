import json
from pathlib import Path

import pytest
from review_fakes import make_review, with_map
from test_policy_classes import feature_review, whole

from jigane.figures.models import FigureKind, TableFigure
from jigane.findings.discard import ShardConcerns
from jigane.groups.definitions import GroupId
from jigane.reviews.lifecycle import Review
from jigane.skills import policy_regex, sharded
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_figures import REGISTRY, FigureSlot, diagrams_of
from jigane.skills.shards import FullTextShard, Shard, Unit
from jigane.storage.store import ReviewKind

NINE = "aa|bb|cc|dd|ee|ff|gg|hh|ii"
CATASTROPHIC = "(a+)+$"
BRANCHY = f'import re\nre.sub(r"({NINE})", "", x)\n'
LONG = "a" * 70


def diff_review(tmp_path: Path, before: str, after: str) -> Review:
    base = tmp_path / "base"
    head = tmp_path / "head"
    base.mkdir()
    head.mkdir()
    (base / "r.py").write_text(before, encoding="utf-8")
    (head / "r.py").write_text(after, encoding="utf-8")
    return make_review(
        head,
        ["r.py"],
        base_tree=base,
        judgment_map={"segments": [whole("r.py", 9)], "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )


def test_moving_a_regex_does_not_select_it(tmp_path: Path) -> None:
    review = diff_review(
        tmp_path, 'import re\nre.compile(r"a.b")\n', 'import re\n\n\nre.compile(r"a.b")\n'
    )
    assert policy_regex.select_regexes(review).targets == ()
    assert policy_regex.regex_table(review) is None


def test_changing_the_body_selects_it(tmp_path: Path) -> None:
    review = diff_review(
        tmp_path, 'import re\nre.compile(r"a.b")\n', 'import re\nre.compile(r"a.c")\n'
    )
    assert [t.pattern for t in policy_regex.select_regexes(review).targets] == ["a.c"]


def test_added_file_selects_every_regex(tmp_path: Path) -> None:
    base = tmp_path / "base"
    head = tmp_path / "head"
    base.mkdir()
    head.mkdir()
    (head / "n.py").write_text('import re\nre.compile(r"x")\nre.compile(r"y")\n', encoding="utf-8")
    segment = whole("n.py", 3) | {"old_path": None, "old_span": None, "old_line_count": 0}
    review = make_review(
        head,
        ["n.py"],
        base_tree=base,
        judgment_map={"segments": [segment], "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )
    assert [t.pattern for t in policy_regex.select_regexes(review).targets] == ["x", "y"]


def moved_review(tmp_path: Path, before: str, after: str) -> Review:
    """old/r.py を new/r.py へ移したレビュー。移動の写しはファイルの対の記録に持たせる。"""
    base = tmp_path / "base"
    head = tmp_path / "head"
    (base / "old").mkdir(parents=True)
    (head / "new").mkdir(parents=True)
    (base / "old" / "r.py").write_text(before, encoding="utf-8")
    (head / "new" / "r.py").write_text(after, encoding="utf-8")
    segment = whole("new/r.py", 9) | {"old_path": "old/r.py"}
    review = make_review(
        head,
        ["new/r.py"],
        base_tree=base,
        judgment_map={"segments": [segment], "regions": [], "line_counts": {}},
        kind=ReviewKind.RANGE,
    )
    directory = review.id.directory()
    directory.mkdir(parents=True)
    (directory / "pairs.json").write_text(json.dumps([["old/r.py", "new/r.py"]]), encoding="utf-8")
    return review


def test_a_moved_file_does_not_select_its_unchanged_regexes(tmp_path: Path) -> None:
    source = 'import re\nre.compile(r"a.b")\nre.compile(name)\n'
    review = moved_review(tmp_path, source, source)
    selection = policy_regex.select_regexes(review)
    assert selection.targets == ()
    assert selection.composed_count == 0
    assert policy_regex.regex_table(review) is None


def test_a_moved_file_selects_only_the_regex_whose_body_changed(tmp_path: Path) -> None:
    review = moved_review(
        tmp_path,
        'import re\nre.compile(r"a.b")\nre.compile(r"x")\n',
        'import re\nre.compile(r"a.b")\nre.compile(r"y")\n',
    )
    assert [t.pattern for t in policy_regex.select_regexes(review).targets] == ["y"]


def test_an_added_file_selects_its_regexes_even_when_another_file_moved(tmp_path: Path) -> None:
    review = moved_review(
        tmp_path, 'import re\nre.compile(r"a")\n', 'import re\nre.compile(r"a")\n'
    )
    (Path(review.repository_root) / "n.py").write_text(
        'import re\nre.compile(r"a")\nre.compile(c)\n', encoding="utf-8"
    )
    directory = review.id.directory()
    (directory / "pairs.json").write_text(
        json.dumps([["old/r.py", "new/r.py"], [None, "n.py"]]), encoding="utf-8"
    )
    added = whole("n.py", 3) | {"old_path": None, "old_span": None, "old_line_count": 0}
    stored = review.map_kind.to_dict()
    review = with_map(review, {**stored, "segments": [*stored["segments"], added]})
    selection = policy_regex.select_regexes(review)
    assert [(t.path, t.pattern) for t in selection.targets] == [("n.py", "a")]
    assert selection.composed_count == 1


def test_feature_review_selects_every_regex_and_counts_composed(tmp_path: Path) -> None:
    source = 'import re\nre.compile(r"x")\nre.compile(name)\n'
    selection = policy_regex.select_regexes(feature_review(tmp_path, "r.py", source))
    assert [t.pattern for t in selection.targets] == ["x"]
    assert selection.composed_count == 1


def test_table_rows_tags_and_header(tmp_path: Path) -> None:
    figure = policy_regex.regex_table(feature_review(tmp_path, "r.py", BRANCHY))
    assert isinstance(figure, TableFigure)
    assert figure.kind == FigureKind.TABLE
    assert figure.header == ("位置", "書き場所", "重み", "札", "本文")
    (row,) = figure.rows
    position, call, weight, tags, body = row.cells
    assert (position, call, body) == ("r.py:2", "re.sub", f"({NINE})")
    assert weight.isdigit()
    assert tags == "warning R2 分岐 9"
    assert (
        figure.note == "直し方の向き: R2 は、"
        "分岐はプログラム言語の側へ移し、正規表現ごとに分けて if や表で振り分ける。"
    )


def test_body_is_cut_at_sixty_characters_with_an_ellipsis(tmp_path: Path) -> None:
    figure = policy_regex.regex_table(
        feature_review(tmp_path, "r.py", f'import re\nre.compile(r"{LONG}")\n')
    )
    assert figure is not None
    assert figure.rows[0].cells[4] == "a" * 60 + "…"
    assert figure.rows[0].cells[3] == ""


def test_unreadable_regex_shows_the_reason(tmp_path: Path) -> None:
    figure = policy_regex.regex_table(
        feature_review(tmp_path, "r.py", 'import re\nre.compile(r"(ab")\n')
    )
    assert figure is not None
    weight, tags = figure.rows[0].cells[2:4]
    assert weight == ""
    assert tags.startswith("読めない（")


def test_note_counts_composed_sites(tmp_path: Path) -> None:
    figure = policy_regex.regex_table(
        feature_review(tmp_path, "r.py", "import re\nre.compile(a)\nre.compile(b)\n")
    )
    assert figure is not None
    assert figure.rows == ()
    assert figure.note is not None
    assert "2 件" in figure.note
    assert not figure.is_drawable()


def test_none_without_regexes(tmp_path: Path) -> None:
    review = feature_review(tmp_path, "m.py", "x = 1\n")
    assert policy_regex.regex_table(review) is None
    assert "regex" not in [p.key for p in diagrams_of(review).figures]


def test_registered_after_regions(tmp_path: Path) -> None:
    entry = next(e for e in REGISTRY if e.key == "regex")
    assert entry.title == "正規表現の表"
    assert entry.slot == FigureSlot.AFTER_REGIONS
    figures = diagrams_of(feature_review(tmp_path, "r.py", BRANCHY)).figures
    assert "regex" in [p.key for p in figures]


def test_cells_join_short_names_and_note_lists_each_direction_once(tmp_path: Path) -> None:
    deep = "((((a))))"
    source = (
        f'import re\nre.sub(r"({NINE})", "", x)\nre.compile(r"{deep}")\n'
        f're.sub(r"({NINE})x", "", x)\nre.compile(r"{CATASTROPHIC}")\n'
    )
    figure = policy_regex.regex_table(feature_review(tmp_path, "r.py", source))
    assert figure is not None
    cells = [row.cells[3] for row in figure.rows]
    assert "nit R4 入れ子 4 段、nit R5 捕獲 4 個" in cells
    assert "warning R7 バックトラック" in cells
    assert all("<br>" not in cell for cell in cells)
    assert figure.note is not None
    assert "\n" not in figure.note
    assert figure.note.startswith("直し方の向き: R2 は、")
    directions = figure.note.removeprefix("直し方の向き: ").split("。")[:-1]
    names = [d.split(" は、")[0] for d in directions]
    assert names == sorted(set(names))
    assert "R4 は、内側を名前のある正規表現に分ける" in directions


def test_description_has_no_backticks() -> None:
    assert "`" not in policy_regex.INTRO


def test_selection_runs_once_for_many_shards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = feature_review(tmp_path, "r.py", BRANCHY)
    calls: list[int] = []
    original = policy_regex.select_regexes

    def counting(target: Review) -> policy_regex.RegexSelection:
        calls.append(1)
        return original(target)

    monkeypatch.setattr(policy_regex, "select_regexes", counting)
    policy_regex._last_judged = None
    for _ in range(3):
        policy_regex.shard_checks("micro", review, ["r.py"])
    policy_regex.regex_table(review)
    assert len(calls) == 1


def shard_of(path: str) -> Shard:
    return FullTextShard((Unit(path, "body", 4),))


def test_shard_without_tags_says_none(tmp_path: Path) -> None:
    review = feature_review(tmp_path, "r.py", 'import re\nre.compile(r"x")\n')
    for name in ("micro", "security"):
        assert policy_regex.shard_checks(name, review, ["r.py"]) == "なし"
        assert "\nなし" in sharded.build_shard_prompt(
            GroupId(name),
            AssemblyInput.load(review),
            shard_of("r.py"),
            1,
            1,
            concerns=ShardConcerns(),
        )


def test_rules_go_to_their_own_group_only(tmp_path: Path) -> None:
    source = f'import re\nre.sub(r"({NINE})", "", x)\nre.compile(r"{CATASTROPHIC}")\n'
    review = feature_review(tmp_path, "r.py", source)
    micro = policy_regex.shard_checks("micro", review, ["r.py"])
    security = policy_regex.shard_checks("security", review, ["r.py"])
    assert "R2" in micro
    assert "R7" not in micro
    assert security == "- r.py:3 R7 warning (a+)+$"
    assert policy_regex.shard_checks("micro", review, ["other.py"]) == "なし"
    prompt = sharded.build_shard_prompt(
        GroupId.SECURITY,
        AssemblyInput.load(review),
        shard_of("r.py"),
        1,
        1,
        concerns=ShardConcerns(),
    )
    assert "### 決定的な検査の札" in prompt
    assert "- r.py:3 R7 warning (a+)+$" in prompt


def test_r6_is_not_tagged_in_test_files(tmp_path: Path) -> None:
    source = 'import re\nre.sub(r"foo", "", x)\n'
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain = policy_regex.regex_table(feature_review(tmp_path / "a", "m.py", source))
    in_test = policy_regex.regex_table(feature_review(tmp_path / "b", "test_m.py", source))
    assert isinstance(plain, TableFigure)
    assert isinstance(in_test, TableFigure)
    assert "R6" in plain.rows[0].cells[3]
    assert "R6" not in in_test.rows[0].cells[3]
