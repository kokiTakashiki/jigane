from pathlib import Path

import pytest
from fakes import MicroFake

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.unreadable_notice import occurrence_text, unreadable_text
from jigane.gates.tree_side import TreeSide
from jigane.judgment_map.unreadable_constructs import (
    ConstructKind,
    Occurrence,
    UnreadableConstructs,
)
from jigane.languages import registry
from jigane.languages.base import parse
from jigane.languages.swift_nodes import IdentifierName, OperatorName, function_name_of
from jigane.reviews.lifecycle import create_feature_review

NOTICE = "読めない記述が、判断の地図の区間のファイルに含まれています。"


def _new_files(monkeypatch: pytest.MonkeyPatch, root: Path, files: dict[str, str]) -> int:
    for relative, body in files.items():
        target = root / "src" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    monkeypatch.chdir(root)
    registry.set_states(None)
    return main(["new", "files", "src", "--name", "n"])


def _stderr(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    files: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> str:
    assert _new_files(monkeypatch, root, files) == EXIT_OK
    return capsys.readouterr().err


def test_syntax_error_is_reported_with_path_and_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    err = _stderr(monkeypatch, tmp_path, {"A.swift": "let x = 1\nfunc (x: Int) {}\n"}, capsys)
    assert NOTICE in err
    assert "構文エラー（1 件）: src/A.swift:2" in err


def test_swift_operator_function_is_reported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = (
        "struct V {\n  static func + (l: V, r: V) -> V { l }\n"
        "  func `class`() {}\n  func f<T>() {}\n}\n"
    )
    err = _stderr(monkeypatch, tmp_path, {"V.swift": body}, capsys)
    assert "名前を取れない演算子関数（Swift）（1 件）: src/V.swift:2" in err
    assert "構文エラー" not in err


def test_csharp_statement_directly_in_class_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "class C {\n  Foo.Bar(x);\n  int ok = 1;\n  void M() { Foo.Bar(x); }\n}\n"
    err = _stderr(monkeypatch, tmp_path, {"C.cs": body}, capsys)
    assert "名前を取れない宣言子（C#。クラス直下の `Foo.Bar(x);` など）（1 件）: src/C.cs:2" in err


def test_clean_files_print_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    err = _stderr(monkeypatch, tmp_path, {"A.swift": "func f() {}\n", "a.py": "x = 1\n"}, capsys)
    assert NOTICE not in err


def test_function_name_kinds_for_swift() -> None:
    adapter = registry.adapter_for("A.swift")
    assert adapter is not None
    source = b"func f() {}\nfunc + (a: Int) {}\nfunc `class`() {}\n"
    root = parse(adapter, source).root_node
    names = [function_name_of(n) for n in root.children if n.type == "function_declaration"]
    assert [type(n) for n in names] == [IdentifierName, OperatorName, IdentifierName]


BROKEN_SWIFT = {"A.swift": "func (x: Int) {}\n"}


def _create(root: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> str:
    for relative, body in files.items():
        target = root / "src" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    monkeypatch.chdir(root)
    registry.set_states(None)
    return str(create_feature_review(root, ["src"], name="u").id)


def _run(name: str, answers: list[str]) -> tuple[int, MicroFake]:
    fake = MicroFake({1: []})

    def read(_prompt: str) -> str:
        return answers.pop(0)

    code = main(["run", name, "micro"], invoke=fake, is_terminal=lambda: True, read_answer=read)
    return code, fake


def test_run_asks_once_for_unreadable_constructs_only_and_yes_launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    name = _create(tmp_path, monkeypatch, BROKEN_SWIFT)
    answers = ["y", "y"]
    code, fake = _run(name, answers)
    assert code == EXIT_OK
    assert fake.calls != []
    assert answers == []
    assert NOTICE in capsys.readouterr().err


def test_run_declining_unreadable_constructs_never_invokes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _create(tmp_path, monkeypatch, BROKEN_SWIFT)
    code, fake = _run(name, ["y", "n"])
    assert code != EXIT_OK
    assert fake.calls == []


def test_run_merges_unparsed_notice_and_unreadable_notice_into_one_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    name = _create(tmp_path, monkeypatch, {**BROKEN_SWIFT, "app.js": "let x = 1;\n"})
    answers = ["y", "y"]
    code, _ = _run(name, answers)
    assert code == EXIT_OK
    assert answers == []
    err = capsys.readouterr().err
    assert err.index("解析しない言語のファイル") < err.index(NOTICE)


def test_run_without_unreadable_constructs_asks_only_the_cost_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _create(tmp_path, monkeypatch, {"a.py": "x = 1\n"})
    answers = ["y"]
    code, _ = _run(name, answers)
    assert code == EXIT_OK
    assert answers == []


def test_estimate_prints_the_notice_and_keeps_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    name = _create(tmp_path, monkeypatch, BROKEN_SWIFT)
    capsys.readouterr()
    assert main(["estimate", name, "macro", "--json"]) == EXIT_OK
    shown = capsys.readouterr()
    assert NOTICE in shown.err
    assert NOTICE not in shown.out


def test_occurrence_marks_before_tree_and_strips_control_characters() -> None:
    syntax = ConstructKind.SYNTAX_ERROR
    assert occurrence_text(Occurrence(syntax, "a/B.swift", 3, TreeSide.BEFORE, "")) == (
        "変更前 a/B.swift:3"
    )
    assert "\x1b" not in occurrence_text(
        Occurrence(syntax, "a/\x1b[31mB.swift", 3, TreeSide.AFTER, "")
    )


def test_unreadable_text_is_empty_when_nothing_is_found() -> None:
    assert unreadable_text(UnreadableConstructs()) == []
