import json
from collections.abc import Callable
from pathlib import Path

import pytest
from language_plugin_support import builtin_states_without_grammar

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.subcommands import estimate as estimate_command
from jigane.commands.subcommands import new as new_command
from jigane.languages import registry
from jigane.reviews.lifecycle import create_feature_review

FETCH_SWIFT = "`jigane languages fetch swift`"


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, body in files.items():
        target = root / "src" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


def _swift_grammar_absent() -> None:
    registry.set_states(builtin_states_without_grammar("swift"))


def _new_files(monkeypatch: pytest.MonkeyPatch, root: Path, extra: list[str]) -> int:
    """`jigane new files` を呼ぶ。"""
    monkeypatch.chdir(root)
    return main(["new", "files", "src", "--name", "n", *extra])


def _new_files_swift_absent(monkeypatch: pytest.MonkeyPatch, root: Path, extra: list[str]) -> int:
    """swift の文法が無い状態でレビューを作る。"""
    monkeypatch.chdir(root)
    _swift_grammar_absent()
    return main(["new", "files", "src", "--name", "n", *extra])


def _silence_new_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(new_command, "print_unparsed_notice", lambda _review: None)


def _comparable(out: str) -> object:
    """作り直したレビューの出力を比べる形。作った時刻は作るたびに違うので除く。"""
    if not out.startswith("{"):
        # 人が読む形の出力は時刻を含まない
        return out
    shown = json.loads(out)
    shown.pop("created_at")
    return shown


def _delete_review(root: Path) -> None:
    registry.set_states(None)
    assert main(["delete", f"{root.name}:n", "--yes"]) == EXIT_OK


@pytest.mark.parametrize("extra", [[], ["--json"]])
def test_new_notices_grammar_absent_language_without_changing_the_result(
    extra: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(tmp_path, {"a.py": "x = 1\n", "A.swift": "let x = 1\n"})
    code = _new_files_swift_absent(monkeypatch, tmp_path, extra)
    shown = capsys.readouterr()
    _delete_review(tmp_path)
    capsys.readouterr()
    _silence_new_notice(monkeypatch)
    baseline_code = _new_files_swift_absent(monkeypatch, tmp_path, extra)
    baseline = capsys.readouterr()
    assert (code, _comparable(shown.out)) == (baseline_code, _comparable(baseline.out))
    assert "解析しない言語" not in baseline.err
    assert "  swift（1 件）: src/A.swift" in shown.err
    assert FETCH_SWIFT in shown.err


def test_new_notices_adapterless_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(tmp_path, {"a.py": "x = 1\n", "app.js": "let x = 1;\n"})
    code = _new_files(monkeypatch, tmp_path, [])
    err = capsys.readouterr().err
    assert code == EXIT_OK
    assert "  .js（1 件）: src/app.js" in err
    assert "docs/adding-languages.md" in err
    assert "`jigane languages enable`" in err


def test_new_prints_nothing_for_parsed_languages_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(tmp_path, {"a.py": "x = 1\n", "README.md": "# x\n"})
    code = _new_files(monkeypatch, tmp_path, [])
    assert code == EXIT_OK
    assert capsys.readouterr().err == ""


def _estimate(review_id: str, *extra: str) -> int:
    return main(["estimate", review_id, "macro", *extra])


@pytest.fixture
def make_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[dict[str, str]], str]:
    def make(files: dict[str, str]) -> str:
        _write(tmp_path, files)
        monkeypatch.chdir(tmp_path)
        return str(create_feature_review(tmp_path, ["src"], name="e").id)

    return make


@pytest.mark.parametrize("extra", [[], ["--json"]])
def test_estimate_notices_grammar_absent_language_without_changing_the_result(
    extra: list[str],
    make_review: Callable[[dict[str, str]], str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    review_id = make_review({"a.py": "x = 1\n", "A.swift": "let x = 1\n"})
    _swift_grammar_absent()
    code = _estimate(review_id, *extra)
    shown = capsys.readouterr()
    monkeypatch.setattr(estimate_command, "print_unparsed_notice", lambda _review: None)
    baseline_code = _estimate(review_id, *extra)
    baseline = capsys.readouterr()
    assert (code, shown.out) == (baseline_code, baseline.out)
    assert "解析しない言語" not in baseline.err
    assert "  swift（1 件）: src/A.swift" in shown.err
    assert FETCH_SWIFT in shown.err
    assert "docs/adding-languages.md" not in shown.err


def test_estimate_notices_adapterless_extension(
    make_review: Callable[[dict[str, str]], str], capsys: pytest.CaptureFixture[str]
) -> None:
    review_id = make_review({"a.py": "x = 1\n", "app.js": "let x = 1;\n"})
    assert _estimate(review_id) == EXIT_OK
    err = capsys.readouterr().err
    assert "  .js（1 件）: src/app.js" in err
    assert "docs/adding-languages.md" in err
    assert "`jigane languages enable`" in err


def test_estimate_prints_nothing_for_parsed_languages_only(
    make_review: Callable[[dict[str, str]], str], capsys: pytest.CaptureFixture[str]
) -> None:
    review_id = make_review({"a.py": "x = 1\n", "data.json": "{}\n"})
    assert _estimate(review_id) == EXIT_OK
    assert capsys.readouterr().err == ""
