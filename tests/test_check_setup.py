import sys
from pathlib import Path
from types import ModuleType

import pytest
from script_loader import import_script

from jigane.languages import grammars


def load_script() -> ModuleType:
    return import_script("check_setup")


def test_missing_virtualenv_stops_with_one_line_guidance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = load_script().main(["--env", str(tmp_path)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert captured.err.startswith("作業ツリーが setup されていない: ")
    assert "make setup を走らせてください（Windows は just setup）。" in captured.err


def test_missing_grammar_files_stop_with_one_line_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_script()
    # 環境の python は今の python を借り、文法の置き場だけを空の場所へ向ける
    monkeypatch.setattr(module, "_venv_python", lambda _root: Path(sys.executable))
    empty = tmp_path / "empty-grammars"
    probe_source = (
        "from pathlib import Path\n"
        "from jigane.languages import grammars\n"
        f"grammars.cache_root = lambda: Path({str(empty)!r})\n"
        "root = grammars.cache_root()\n"
        "for name in grammars.GRAMMARS:\n"
        "    if grammars.library_path(root, name) is None:\n"
        "        print(name)\n"
    )
    problem = module.missing_in(tmp_path, probe_source)
    assert problem is not None
    assert "文法 python" in problem
    assert not empty.exists()


def test_complete_environment_passes_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = load_script()
    monkeypatch.setattr(module, "_venv_python", lambda _root: Path(sys.executable))
    # 文法の置き場を一時の場所へ向け、全部の文法の共有ライブラリが揃った状態を作る
    root = tmp_path / "grammars"
    library_dir = grammars.library_dir(root)
    library_dir.mkdir(parents=True)
    for name in grammars.GRAMMARS:
        (library_dir / f"libtree_sitter_{name}.so").touch()
    monkeypatch.setattr(
        module,
        "_PROBE",
        "from pathlib import Path\n"
        "from jigane.languages import grammars\n"
        f"root = Path({str(root)!r})\n"
        "for name in grammars.GRAMMARS:\n"
        "    if grammars.library_path(root, name) is None:\n"
        "        print(name)\n",
    )
    exit_code = module.main(["--env", str(tmp_path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert captured.err == ""
