from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from fakes import MicroFake

from jigane.commands import main
from jigane.commands.consent import NON_SOURCE_SUFFIXES, unadapted_files, unadapted_text
from jigane.commands.exit_codes import EXIT_OK
from jigane.git.process import CONSENT_PROMPT_END
from jigane.judgment_map.map_kind import CodeMap, DesignDocMap
from jigane.reviews.lifecycle import create_feature_review, load_review, resolve_review

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


def test_unadapted_files_ask_a_second_question_and_yes_launches(
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
    assert "アダプタは `jigane languages` で足せます。" in err


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


def test_no_unadapted_files_asks_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    name = _review(tmp_path, monkeypatch, {"a.py": "x = 1\n"})
    prompts: list[str] = []
    code, _ = _run(name, ["y"], prompts)
    assert code == EXIT_OK
    assert len(prompts) == 1
    assert "アダプタの無い言語" not in capsys.readouterr().err


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
    assert unadapted_files(load_review(resolve_review(name))) == {}
    prompts: list[str] = []
    code, _ = _run(name, ["y"], prompts)
    assert code == EXIT_OK
    assert len(prompts) == 1


def test_design_doc_map_does_not_count_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _review(tmp_path, monkeypatch, {"app.js": "let x = 1;\n"})
    review = load_review(resolve_review(name))
    assert unadapted_files(review) == {".js": ["src/app.js"]}
    assert isinstance(review.map_kind, CodeMap)
    design = replace(review, map_kind=DesignDocMap(review.map_kind.table, review.map_kind))
    assert unadapted_files(design) == {}


def test_non_source_suffix_table_is_fixed() -> None:
    assert sorted(NON_SOURCE_SUFFIXES) == sorted(
        [".md", ".markdown", ".json", ".yaml", ".yml", ".toml", ".txt", ".rst", ".csv", ".lock"]
    )


@pytest.mark.parametrize("suffix", sorted(NON_SOURCE_SUFFIXES))
def test_every_non_source_suffix_is_left_uncounted(
    suffix: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _review(tmp_path, monkeypatch, {"a.py": "x = 1\n", f"data{suffix}": "x\n"})
    assert unadapted_files(load_review(resolve_review(name))) == {}


def test_file_names_are_shown_without_terminal_control_sequences() -> None:
    lines = unadapted_text({".js": ["src/\x1b[31mevil\nname.js"]})
    assert lines[1] == "  .js（1 件）: src/evil name.js"
