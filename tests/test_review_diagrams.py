import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from fakes import register_no_sequence_adapter
from review_fakes import with_map

from jigane.commands import main
from jigane.figures.models import (
    FileState,
    NoteText,
    Panel,
    SequenceFigure,
    SequenceFlow,
    TreeFigure,
)
from jigane.policy_materials import changed_files
from jigane.policy_materials.changed_files import FileChange, PrebuiltSection
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.sequence.review_diagrams import sequences_of
from jigane.skills.policy_figures import DiagramsResult, diagrams_of

CSHARP_FIXTURE = Path(__file__).parent / "fixtures" / "sequence" / "csharp" / "Rules.cs"
CHAIN_FIXTURE = Path(__file__).parent / "fixtures" / "sequence" / "csharp" / "Chain.cs"


def entry(*, added: bool = False, lines: int = 3) -> FileChange:
    return FileChange(FileState.ADDED if added else FileState.CHANGED, lines)


def _feature_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]
) -> Review:
    root = tmp_path / "shop"
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    monkeypatch.chdir(root)
    return create_feature_review(root, ["src"], "p")


@pytest.fixture
def review_with_regions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    review = _feature_review(tmp_path, monkeypatch, {"src/a.py": "x = 1\n"})
    regions = [{**review.map_kind.to_dict()["regions"][0], "id": f"J{n}"} for n in (1, 2)]
    return with_map(review, {**review.map_kind.to_dict(), "regions": regions})


def test_mermaid_has_no_fence_and_the_prompt_text_wraps_the_same_source() -> None:
    files = {"src/a.py": entry(), "src/b.py": entry(added=True)}
    source = changed_files.tree_mermaid(files)
    assert source.startswith("flowchart LR")
    assert "```" not in source
    figure = TreeFigure(Panel("変更後の木", source), changed_files.file_summaries(files))
    assert f"\n\n```mermaid\n{source}\n```\n" in figure.text()


def test_class_definitions_carry_no_colors() -> None:
    # Mermaid は classDef を形の要素の style に !important 付きで書く。
    # 色を持たせると、画面のテーマで塗り替えられなくなる
    files = {"src/a.py": entry(), "src/b.py": entry(added=True)}
    source = changed_files.tree_mermaid(files)
    definitions = [line for line in source.splitlines() if line.strip().startswith("classDef")]
    assert len(definitions) == 4
    assert not [line for line in definitions if "fill" in line or "#" in line or "color" in line]


def test_nodes_carry_region_tags_and_cap_them() -> None:
    files = {"src/a.py": entry(), "src/b.py": entry()}
    tags = {"src/a.py": ["J1", "J2", "J3", "J4", "J5"], "src/b.py": ["J6"]}
    source = changed_files.tree_mermaid(files, tags)
    assert "a.py<br/>変更・3 行<br/>J1 J2 J3 ほか 2 件" in source
    assert "b.py<br/>変更・3 行<br/>J6" in source


def test_region_tags_group_regions_by_path(review_with_regions: Review) -> None:
    assert changed_files.region_tags(review_with_regions) == {"src/a.py": ["J1", "J2"]}


def test_prompt_diagram_is_the_screen_source(review_with_regions: Review) -> None:
    files = changed_files.file_changes(review_with_regions)
    tags = changed_files.region_tags(review_with_regions)
    source = changed_files.tree_mermaid(files, tags)
    assert source in changed_files.materials_sections(review_with_regions)[PrebuiltSection.TREE]
    assert "J1 J2" in source


def test_hostile_file_names_do_not_break_the_source_or_become_html() -> None:
    hostile = 'we<ird>/a"b<img src=x onerror=alert(1)>[c]&#d|e{f}.py'
    files = {hostile: entry(), "src/ok.py": entry()}
    source = changed_files.tree_mermaid(files, {hostile: ['J"<1>']})
    labels = [
        line.split('["', 1)[1].rsplit('"]', 1)[0] for line in source.splitlines() if '["' in line
    ]
    # ファイル 2 つ、ディレクトリ 2 つ、根 1 つ
    assert len(labels) == 5
    for label in labels:
        stripped = label.replace("<br/>", "")
        assert "<" not in stripped
        assert ">" not in stripped
        assert '"' not in label
        assert "[" not in label.replace("<br/>", "")
    assert "#lt;img src=x onerror=alert(1)#gt;" in source
    assert "#35;d" in source
    assert "#amp;" in source


def test_newline_in_a_name_does_not_split_the_node() -> None:
    source = changed_files.tree_mermaid({"src/a\nb.py": entry()})
    assert sum('["a b.py' in line for line in source.splitlines()) == 1


def _materials(notes: Iterable[NoteText]) -> list[str]:
    return [note.material for note in notes]


def _sequence_figure_of(result: DiagramsResult) -> SequenceFigure:
    return next(p.figure for p in result.figures if isinstance(p.figure, SequenceFigure))


def _tree_figure_of(result: DiagramsResult) -> TreeFigure:
    return next(p.figure for p in result.figures if isinstance(p.figure, TreeFigure))


def test_review_without_a_sequence_language_returns_no_sequence_and_one_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _feature_review(tmp_path, monkeypatch, {"src/A.nosq": "class A {}\n"})
    # 登録は作成の後。作成時の言語の表（flaky の catalog）は登録済みの全言語を要求する
    register_no_sequence_adapter(monkeypatch)
    result = diagrams_of(review)
    sequence = _sequence_figure_of(result)
    assert sequence.flows == ()
    assert sequence.notes == (
        NoteText(
            "変更したファイルの言語に、呼び出しの抽出がまだ無い。",
            "変更したファイルの言語には、呼び出しの抽出がまだありません。",
        ),
    )
    tree = _tree_figure_of(result).after
    assert tree.title == "変更後の木"
    assert tree.mermaid.startswith("flowchart LR")
    assert set(result.to_json()) == {"figures", "unsupported"}


def test_java_review_returns_a_sequence_for_the_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = (
        "class Service {\n"
        "    private final Repo repo;\n"
        "    Service(Repo repo) {\n"
        "        this.repo = repo;\n"
        "    }\n"
        "    void run() {\n"
        "        repo.save();\n"
        "    }\n"
        "}\n"
    )
    repo = "class Repo {\n    void save() {\n        int x = 1;\n    }\n}\n"
    review = _feature_review(
        tmp_path, monkeypatch, {"src/Service.java": service, "src/Repo.java": repo}
    )
    sequences = _sequence_figure_of(diagrams_of(review)).flows
    assert sequences
    assert all(s.panels[0].mermaid.startswith("sequenceDiagram") for s in sequences)
    assert any("P0->>P1: save()" in s.panels[0].mermaid for s in sequences)


def test_csharp_review_returns_sequences_per_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _feature_review(
        tmp_path,
        monkeypatch,
        {
            "src/Rules.cs": CSHARP_FIXTURE.read_text(encoding="utf-8"),
            "src/Chain.cs": CHAIN_FIXTURE.read_text(encoding="utf-8"),
        },
    )
    result = diagrams_of(review)
    sequences = _sequence_figure_of(result).flows
    # 呼び出しの多い Runner.Solo（3 本）が、Runner.Run（2 本）より先に来る
    assert [s.title.split("（")[0] for s in sequences] == ["Runner.Solo", "Runner.Run"]
    # 機能レビュー式は一つのパネルで、注記は無い
    assert all(s.note is None for s in sequences)
    assert all([p.title for p in s.panels] == ["シーケンス図"] for s in sequences)
    assert all(s.panels[0].mermaid.startswith("sequenceDiagram") for s in sequences)
    assert all("Chain.cs" in s.title for s in sequences)
    notes = _sequence_figure_of(result).notes
    # 変更に届く呼び出しの無い 3 件と、受け手の型を解けない呼び出しを持つ 1 件に分かれる
    assert notes[0].material.startswith("変更に関わる呼び出しの無い入口 3 件は図にしない: Rules.")
    assert notes[1].material.startswith(
        "呼び先を解けない呼び出しがある入口 1 件は図にしない: Rules."
    )


def test_csharp_review_without_entries_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _feature_review(tmp_path, monkeypatch, {"src/Empty.cs": "class Empty { }\n"})
    result = diagrams_of(review)
    sequence = _sequence_figure_of(result)
    assert sequence.flows == ()
    assert _materials(sequence.notes) == ["変更した宣言も、変更したファイルを読む宣言も無い。"]


def _diagram(root: str, calls: int) -> dict[str, Any]:
    participants = [
        {"alias": "A", "qualified_name": "A", "display": "A", "external": False, "regions": []}
    ]
    arrows = [
        {
            "sender": "A",
            "receiver": "A",
            "name": f"m{n}",
            "depth": 1,
            "path": "a.cs",
            "line": n,
            "status": "resolved",
            "region": None,
        }
        for n in range(calls)
    ]
    return {
        "root": root,
        "path": "a.cs",
        "line": 1,
        "labels": [],
        "participants": participants,
        "arrows": arrows,
        "notes": [],
    }


def test_entries_without_calls_become_a_note_and_the_rest_sort_by_call_count() -> None:
    diagrams = [
        _diagram("E0", 0),
        _diagram("E1", 1),
        _diagram("E2", 0),
        _diagram("E3", 3),
        _diagram("E4", 1),
    ]
    figure = sequences_of({"diagrams": diagrams}, 1)
    assert [s.title.split("（")[0] for s in figure.flows] == ["E3", "E1", "E4"]
    assert list(figure.notes) == [
        NoteText(
            "呼び出しの無い入口 2 件は図にしない: E0、E2",
            "呼び出しのない入口 2 件は図にしません: E0、E2",
        )
    ]


def test_many_entries_without_calls_are_named_in_part() -> None:
    diagrams = [_diagram(f"E{n}", 0) for n in range(5)] + [_diagram("X", 1)]
    figure = sequences_of({"diagrams": diagrams}, 1)
    assert len(figure.flows) == 1
    assert _materials(figure.notes) == [
        "呼び出しの無い入口 5 件は図にしない: E0、E1、E2、ほか 2 件"
    ]


def test_all_entries_without_calls_leave_no_sequence_and_a_note() -> None:
    figure = sequences_of({"diagrams": [_diagram("E0", 0), _diagram("E1", 0)]}, 2)
    assert figure.flows == ()
    assert figure.notes[-1].display == "変更した宣言はありますが、他の型とのやり取りがありません。"
    assert _materials(figure.notes) == [
        "呼び出しの無い入口 2 件は図にしない: E0、E1",
        "変更した宣言はあるが、他の型とのやり取りが無い。",
    ]


def test_no_diagram_reason_follows_the_changed_declaration_count() -> None:
    empty: dict[str, Any] = {"diagrams": []}
    assert _materials(sequences_of(empty, 0).notes) == [
        "変更した宣言も、変更したファイルを読む宣言も無い。"
    ]
    assert _materials(sequences_of(empty, 3).notes) == [
        "変更した宣言はあるが、他の型とのやり取りが無い。"
    ]


def test_data_files_only_say_there_is_no_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 呼び出しを持つ言語ではないので、「抽出がまだ無い」ではなく宣言が無いことを理由にする
    review = _feature_review(tmp_path, monkeypatch, {"src/paths.json": "{}\n"})
    result = diagrams_of(review)
    sequence = _sequence_figure_of(result)
    assert sequence.flows == ()
    assert _materials(sequence.notes) == ["変更した宣言も、変更したファイルを読む宣言も無い。"]


def test_reasons_name_no_language(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty: dict[str, Any] = {"diagrams": []}
    notes = [
        *_sequence_figure_of(
            diagrams_of(_feature_review(tmp_path, monkeypatch, {"src/a.py": "x = 1\n"}))
        ).notes,
        *sequences_of(empty, 0).notes,
        *sequences_of(empty, 1).notes,
    ]
    for note in notes:
        for text in (note.material, note.display):
            assert not any(name in text for name in ("C#", "Python", "Java", "Swift"))


def test_cli_json_matches_diagrams_of(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    review = _feature_review(tmp_path, monkeypatch, {"src/a.py": "x = 1\n"})
    assert main(["diagrams", "--json", str(review.id)]) == 0
    assert json.loads(capsys.readouterr().out) == diagrams_of(review).to_json()


def test_cli_text_fences_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    review = _feature_review(tmp_path, monkeypatch, {"src/A.nosq": "class A {}\n"})
    register_no_sequence_adapter(monkeypatch)
    assert main(["diagrams", str(review.id)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "```mermaid" in lines
    assert lines[0] == "変更後の木"
    assert lines[lines.index("```mermaid") + 1].startswith("flowchart LR")
    assert "変更したファイルの言語には、呼び出しの抽出がまだありません。" in lines


def test_cli_text_fences_each_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    review = _feature_review(
        tmp_path, monkeypatch, {"src/Chain.cs": CHAIN_FIXTURE.read_text(encoding="utf-8")}
    )
    assert main(["diagrams", str(review.id)]) == 0
    out = capsys.readouterr().out
    head = out.split("\nクラス図\n")[0]  # 方針のレビューの図は後ろに付く
    assert head.count("```mermaid") == 1 + len(_sequence_figure_of(diagrams_of(review)).flows)
    assert "sequenceDiagram" in out


def _sequence(
    title: str, mermaid: str, note: NoteText | None = None, regions: tuple[str, ...] = ()
) -> SequenceFlow:
    return SequenceFlow(title, (Panel("変更後", mermaid),), note, regions)


def test_materials_put_region_tagged_sequences_first_and_carry_them_all() -> None:
    plain = [_sequence(f"P{n}", "sequenceDiagram\n    A->>B: f()\n") for n in range(12)]
    tagged = _sequence("T", "sequenceDiagram\n    A->>B: g() [J3]\n", regions=("J3",))
    text = SequenceFigure((*plain, tagged)).text()
    assert text.index("#### T") < text.index("#### P0")
    assert text.count("```mermaid") == 13
    assert "#### P11" in text
    assert "図にしていない" not in text


def test_materials_say_why_when_the_prior_diagram_is_missing_and_when_none_exists() -> None:
    text = SequenceFigure(
        (
            _sequence(
                "E",
                "sequenceDiagram\n",
                NoteText("変更前にこの入口が無い", "変更前にこの入口がありません"),
            ),
        )
    ).text()
    assert "変更前の図は無い: 変更前にこの入口が無い" in text
    assert "図にしていない" not in text
    plain = "変更した宣言も、変更したファイルを読む宣言も無い。"
    assert SequenceFigure(
        (), (NoteText(plain, "変更した宣言も、変更したファイルを読む宣言もありません。"),)
    ).text() == ("変更した宣言も、変更したファイルを読む宣言も無い。")


def test_flow_regions_come_from_the_model_and_match_the_drawn_tags() -> None:
    def diagram(region: str | None) -> dict[str, Any]:
        participants: list[dict[str, Any]] = [
            {"alias": a, "qualified_name": a, "display": a, "external": False, "regions": []}
            for a in ("A", "B")
        ]
        participants[1]["regions"] = ["J7"]
        arrow = {
            "sender": "A",
            "receiver": "B",
            "name": "f",
            "depth": 1,
            "path": "m.py",
            "line": 1,
            "status": "resolved",
            "region": region,
        }
        return {
            "root": "r",
            "path": "m.py",
            "line": 1,
            "labels": [],
            "notes": [],
            "participants": participants,
            "arrows": [arrow],
        }

    after = diagram("J3")
    after["before"] = diagram("J9")
    flows = sequences_of({"side": "both", "diagrams": [after]}, 1).flows
    assert flows[0].regions == ("J7", "J3", "J9")
    for panel in flows[0].panels:
        assert "[J7]" in panel.mermaid
    assert "[J3]" in flows[0].panels[1].mermaid
    assert "[J9]" in flows[0].panels[0].mermaid


def test_entry_with_only_unrelated_calls_is_not_called_callless() -> None:
    unrelated = {**_diagram("R0", 0), "collapsed_calls": 3}
    notes = sequences_of({"diagrams": [unrelated, _diagram("E0", 0)]}, 1).notes
    assert _materials(notes[:2]) == [
        "呼び出しの無い入口 1 件は図にしない: E0",
        "変更に関わる呼び出しの無い入口 1 件は図にしない: R0",
    ]


def test_policy_materials_drop_the_before_diagram_of_tagged_flows_only() -> None:
    both = (
        Panel("変更前", "sequenceDiagram\n    A->>B: old()\n"),
        Panel("変更後", "sequenceDiagram\n    A->>B: new()\n"),
    )
    tagged = SequenceFlow("T", both, None, ("J3",))
    plain = SequenceFlow("P", both, None)
    text = SequenceFigure((tagged, plain)).without_tagged_before().text()
    tagged_block, plain_block = text.split("#### P")
    assert "new()" in tagged_block
    assert "old()" not in tagged_block
    # 札の無い入口は変更前の図を残す
    assert "old()" in plain_block
    assert "new()" in plain_block
