import json
from pathlib import Path

import pytest
from fakes import register_no_sequence_adapter

from jigane.commands import main
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_figures import (
    LANGUAGE_AGNOSTIC,
    REGISTRY,
    FigureSlot,
    diagrams_of,
    section_texts,
)

CSHARP = "class A { }\n"
PYTHON = "x = 1\n"
NOTE = "対応外の言語: C#（jigane の C# の解析は、この表の読み取りにまだ対応していない）。"
NOSEQ_BUILTIN_NOTE = (
    "対応外の言語: no-sequence"
    "（jigane の no-sequence の解析は、この表の読み取りにまだ対応していない）。"
)
BUILTIN_NOTE_CSHARP_SWIFT = (
    "対応外の言語: C#、Swift"
    "（jigane の C#、Swift の解析は、この表の読み取りにまだ対応していない）。"
)
PLUGIN_NOTE = "対応外の言語: no-sequence（入れたプラグインが、この表の読み取りを持たない）。"
BOTH_NOTE = (
    "対応外の言語: Swift（jigane の Swift の解析は、この表の読み取りにまだ対応していない）。"
    " " + PLUGIN_NOTE
)


def _review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> Review:
    root = tmp_path / "shop"
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    monkeypatch.chdir(root)
    return create_feature_review(root, ["src"], "p")


def _unsupported(review: Review) -> dict[str, tuple[str, ...]]:
    return {
        u.key: tuple(language.name for language in u.languages)
        for u in diagrams_of(review).unsupported
    }


def test_csharp_only_change_is_unsupported_for_both_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _review(tmp_path, monkeypatch, {"src/A.cs": CSHARP})
    assert _unsupported(review) == {"commands": ("C#",), "file_paths": ("C#",), "stores": ("C#",)}
    text = macro.diagram_materials_text(AssemblyInput.load(review), macro.policy_sections(review))
    assert NOTE in text


def test_python_only_change_is_not_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _review(tmp_path, monkeypatch, {"src/a.py": PYTHON})
    assert _unsupported(review) == {}
    assert "対応外" not in macro.diagram_materials_text(
        AssemblyInput.load(review), macro.policy_sections(review)
    )


def test_non_language_files_are_not_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _review(tmp_path, monkeypatch, {"src/notes.md": "# x\n"})
    assert _unsupported(review) == {}


def test_mixed_change_names_only_the_unsupported_languages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {"src/a.py": PYTHON, "src/A.cs": CSHARP, "src/B.swift": "struct B {}\n"}
    review = _review(tmp_path, monkeypatch, files)
    assert _unsupported(review)["commands"] == ("C#", "Swift")
    assert _unsupported(review)["stores"] == ("C#", "Swift")
    sections = section_texts(review, FigureSlot.AFTER_TREE)
    assert any(BUILTIN_NOTE_CSHARP_SWIFT in s for s in sections)
    assert not any("Python" in s for s in sections if "対応外" in s)


def test_json_has_unsupported_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    review = _review(tmp_path, monkeypatch, {"src/A.cs": CSHARP})
    assert main(["diagrams", "--json", str(review.id)]) == 0
    payload = json.loads(capsys.readouterr().out)
    entry = next(u for u in payload["unsupported"] if u["key"] == "commands")
    assert entry["languages"] == [{"name": "C#", "builtin": True}]
    assert entry["note"] == NOTE
    assert payload == diagrams_of(review).to_json()


def test_cli_text_prints_the_unsupported_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    review = _review(tmp_path, monkeypatch, {"src/A.cs": CSHARP})
    assert main(["diagrams", str(review.id)]) == 0
    assert NOTE in capsys.readouterr().out


def test_language_without_any_interface_is_unsupported_for_every_interface_figure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 検証の道具（flaky の資料）は組み込みの言語しか知らないので、レビューを作ってから登録する
    review = _review(tmp_path, monkeypatch, {"src/a.nosq": "class A { }\n"})
    register_no_sequence_adapter(monkeypatch)
    keys = (
        "sequence",
        "concurrency",
        "classes",
        "er",
        "discriminators",
        "readers",
        "commands",
        "file_paths",
        "stores",
        "regex",
    )
    # ツリーとシグネチャは言語の口に依らないので、対応外に載らない。
    # クラス図などは SequenceAdapter の索引から宣言を引くので、その口の無い言語では対応外に載る
    assert _unsupported(review) == dict.fromkeys(keys, ("no-sequence",))


def test_prompt_names_the_language_without_an_interface_in_the_new_figures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 検証の道具（flaky の資料）は組み込みの言語しか知らないので、レビューを作ってから登録する
    review = _review(tmp_path, monkeypatch, {"src/a.nosq": "class A { }\n"})
    register_no_sequence_adapter(monkeypatch)
    note = NOSEQ_BUILTIN_NOTE
    after_tree = section_texts(review, FigureSlot.AFTER_TREE)
    after_regions = section_texts(review, FigureSlot.AFTER_REGIONS)

    # 期待値は登録簿から導く。口を持つ図が、置き場ごとに一節ずつ一文を載せる。
    # シーケンス図は事前に組む節なので、置き場の図には数えない
    def with_interface(slot: FigureSlot) -> int:
        return sum(e.slot == slot and e.interface is not LANGUAGE_AGNOSTIC for e in REGISTRY)

    assert sum(note in s for s in after_tree) == with_interface(FigureSlot.AFTER_TREE)
    assert sum(note in s for s in after_regions) == with_interface(FigureSlot.AFTER_REGIONS)
    assert with_interface(FigureSlot.AFTER_TREE) > 0


def test_every_figure_states_its_language_interface() -> None:
    # 既定値に頼らず、12 種の図がすべて自分の口（または言語に依らないこと）を書いている
    assert len(REGISTRY) == 12
    assert all(e.interface is LANGUAGE_AGNOSTIC or isinstance(e.interface, type) for e in REGISTRY)
    assert {e.key for e in REGISTRY if e.interface is LANGUAGE_AGNOSTIC} == {"tree", "signatures"}


def test_sequence_section_names_the_language_without_an_interface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _review(tmp_path, monkeypatch, {"src/a.nosq": "class A { }\n"})
    register_no_sequence_adapter(monkeypatch)
    note = NOSEQ_BUILTIN_NOTE
    text = macro.diagram_materials_text(AssemblyInput.load(review), macro.policy_sections(review))
    sections = [s.rstrip() for s in text.split("### ")]
    sequence = next(s for s in sections if s.startswith("シーケンス図"))
    # 描けるシーケンス図が無くても、対応外の言語があれば一文は残る
    assert sequence.endswith(note)
    tree = next(s for s in sections if s.startswith("変更のツリー図"))
    assert note not in tree


def test_builtin_languages_share_one_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _review(tmp_path, monkeypatch, {"src/A.cs": CSHARP, "src/B.swift": "struct B {}\n"})
    notes = {u.note() for u in diagrams_of(review).unsupported}
    assert notes == {BUILTIN_NOTE_CSHARP_SWIFT}


def test_plugin_language_gets_the_plugin_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _review(tmp_path, monkeypatch, {"src/a.nosq": "class A { }\n"})
    register_no_sequence_adapter(monkeypatch, external=True)
    result = diagrams_of(review)
    assert {u.note() for u in result.unsupported} == {PLUGIN_NOTE}
    assert all(not language.builtin for u in result.unsupported for language in u.languages)


def test_mixed_origins_put_the_builtin_sentence_first_in_every_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    files = {"src/a.nosq": "class A { }\n", "src/B.swift": "struct B {}\n"}
    review = _review(tmp_path, monkeypatch, files)
    register_no_sequence_adapter(monkeypatch, external=True)
    # 資料
    text = macro.diagram_materials_text(AssemblyInput.load(review), macro.policy_sections(review))
    assert BOTH_NOTE in text
    # CLI（文面と JSON）。画面は JSON の note をそのまま出す
    assert main(["diagrams", str(review.id)]) == 0
    assert BOTH_NOTE in capsys.readouterr().out
    assert main(["diagrams", "--json", str(review.id)]) == 0
    payload = json.loads(capsys.readouterr().out)
    # Swift が口を持つ図では外の言語の文だけが残る
    assert {u["note"] for u in payload["unsupported"]} == {BOTH_NOTE, PLUGIN_NOTE}
    entry = next(u for u in payload["unsupported"] if u["key"] == "commands")
    assert entry["note"] == BOTH_NOTE
    assert entry["languages"] == [
        {"name": "Swift", "builtin": True},
        {"name": "no-sequence", "builtin": False},
    ]
