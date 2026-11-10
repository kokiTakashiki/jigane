from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from fakes import MicroFake
from language_plugin_support import builtin_states_without_grammar

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.unparsed_files import (
    NON_SOURCE_SUFFIXES,
    UnparsedFiles,
    unparsed_files,
    unparsed_text,
)
from jigane.git.process import CONSENT_PROMPT_END
from jigane.judgment_map.map_kind import CodeMap, DesignDocMap
from jigane.languages import registry
from jigane.reviews.catalog import load_review
from jigane.reviews.lifecycle import create_feature_review
from jigane.reviews.resolution import resolve_review

QUESTION = f"それでもレビューを実施しますか{CONSENT_PROMPT_END}"


def _review(root: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> str:
    for relative, body in files.items():
        target = root / "src" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    monkeypatch.chdir(root)
    return str(create_feature_review(root, ["src"], name="u").id)


def _ask(answers: list[str], prompts: list[str]) -> Callable[[str], str]:
    def read(prompt: str) -> str:
        prompts.append(prompt)
        return answers.pop(0)

    return read


def _run(review_name: str, answers: list[str], prompts: list[str]) -> tuple[int, MicroFake]:
    fake = MicroFake({1: []})
    code = main(
        ["run", review_name, "micro"],
        invoke=fake,
        is_terminal=lambda: True,
        read_answer=_ask(answers, prompts),
    )
    return code, fake


def test_unparsed_files_ask_a_second_question_and_yes_launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    name = _review(
        tmp_path,
        monkeypatch,
        {"a.py": "x = 1\n", "app.js": "let x = 1;\n", "lib/b.js": "let y = 2;\n"},
    )
    prompts: list[str] = []
    code, fake = _run(name, ["y", "y"], prompts)
    assert code == EXIT_OK
    assert fake.calls != []
    assert prompts[1] == QUESTION
    err = capsys.readouterr().err
    assert "  .js（2 件）: src/app.js, src/lib/b.js" in err
    assert "これらの区間は宣言を読まずに「確認」になります。" in err
    assert "アダプタの無い言語:" in err
    assert "docs/adding-languages.md" in err
    assert "`jigane languages enable`" in err


def test_declining_the_second_question_never_invokes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _review(tmp_path, monkeypatch, {"a.py": "x = 1\n", "app.js": "let x = 1;\n"})
    prompts: list[str] = []
    code, fake = _run(name, ["y", "n"], prompts)
    assert code != EXIT_OK
    assert fake.calls == []
    assert len(prompts) == 2


def test_the_cost_refusal_comes_before_the_second_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _review(tmp_path, monkeypatch, {"app.js": "let x = 1;\n"})
    prompts: list[str] = []
    code, fake = _run(name, ["n"], prompts)
    assert code != EXIT_OK
    assert fake.calls == []
    assert len(prompts) == 1


def test_no_unparsed_files_asks_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    name = _review(tmp_path, monkeypatch, {"a.py": "x = 1\n"})
    prompts: list[str] = []
    code, _ = _run(name, ["y"], prompts)
    assert code == EXIT_OK
    assert len(prompts) == 1
    assert "解析しない言語" not in capsys.readouterr().err


def test_documents_data_extensionless_and_adapted_files_are_not_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        "a.py": "x = 1\n",
        "README.md": "# x\n",
        "data.json": "{}\n",
        "conf.yaml": "a: 1\n",
        "LICENSE": "text\n",
        "Makefile": "all:\n",
    }
    name = _review(tmp_path, monkeypatch, files)
    assert unparsed_files(load_review(resolve_review(name))).is_empty
    prompts: list[str] = []
    code, _ = _run(name, ["y"], prompts)
    assert code == EXIT_OK
    assert len(prompts) == 1


def test_design_doc_map_does_not_count_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _review(tmp_path, monkeypatch, {"app.js": "let x = 1;\n"})
    review = load_review(resolve_review(name))
    assert unparsed_files(review) == UnparsedFiles({}, {".js": ("src/app.js",)})
    assert isinstance(review.map_kind, CodeMap)
    design = replace(review, map_kind=DesignDocMap(review.map_kind.table, review.map_kind))
    assert unparsed_files(design).is_empty


def test_non_source_suffix_table_is_fixed() -> None:
    assert sorted(NON_SOURCE_SUFFIXES) == sorted(
        [".md", ".markdown", ".json", ".yaml", ".yml", ".toml", ".txt", ".rst", ".csv", ".lock"]
    )


@pytest.mark.parametrize("suffix", sorted(NON_SOURCE_SUFFIXES))
def test_every_non_source_suffix_is_left_uncounted(
    suffix: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _review(tmp_path, monkeypatch, {"a.py": "x = 1\n", f"data{suffix}": "x\n"})
    assert unparsed_files(load_review(resolve_review(name))).is_empty


def test_file_names_are_shown_without_terminal_control_sequences() -> None:
    lines = unparsed_text(UnparsedFiles({}, {".js": ("src/\x1b[31mevil\nname.js",)}))
    assert "  .js（1 件）: src/evil name.js" in lines
    assert not any("\x1b" in line for line in lines)


def _set_grammars_absent() -> None:
    """swift と java の文法が無い状態にする。レビューは文法がある状態で先に作る。

    状態の戻しは shared_fixtures の autouse が行う。
    """
    registry.set_states(builtin_states_without_grammar("swift", "java"))


def test_grammar_absent_language_shows_name_and_one_fetch_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _review(
        tmp_path,
        monkeypatch,
        {"a.py": "x = 1\n", "A.swift": "let x = 1\n", "B.java": "class B {}\n"},
    )
    review = load_review(resolve_review(name))
    _set_grammars_absent()
    found = unparsed_files(review)
    assert found == UnparsedFiles({"swift": ("src/A.swift",), "java": ("src/B.java",)}, {})
    lines = unparsed_text(found)
    assert "  swift（1 件）: src/A.swift" in lines
    assert "  java（1 件）: src/B.java" in lines
    assert "`jigane languages fetch java swift` で取得すると解析できます。" in lines
    assert not any("adding-languages" in line for line in lines)


def test_grammar_absent_and_no_adapter_are_split_correctly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _review(tmp_path, monkeypatch, {"A.swift": "let x = 1\n", "app.js": "let x = 1;\n"})
    review = load_review(resolve_review(name))
    _set_grammars_absent()
    found = unparsed_files(review)
    assert found == UnparsedFiles({"swift": ("src/A.swift",)}, {".js": ("src/app.js",)})
    text = "\n".join(unparsed_text(found))
    assert text.count("docs/adding-languages.md") == 1
    assert text.count("jigane languages fetch swift") == 1
    assert "これらの区間は宣言を読まずに「確認」になります。" in text


def test_run_declining_after_grammar_absent_notice_never_invokes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    name = _review(tmp_path, monkeypatch, {"A.swift": "let x = 1\n"})
    _set_grammars_absent()
    prompts: list[str] = []
    code, fake = _run(name, ["y", "n"], prompts)
    assert code != EXIT_OK
    assert fake.calls == []
    assert prompts[1] == QUESTION
    assert "jigane languages fetch swift" in capsys.readouterr().err
