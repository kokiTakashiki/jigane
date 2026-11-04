import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_script() -> ModuleType:
    path = ROOT / "scripts" / "run_lint_imports.py"
    spec = importlib.util.spec_from_file_location("run_lint_imports", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_lint_imports = load_script()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_main_writes_japanese_as_utf8_and_returns_exit_code(
    monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="\n")

    def fake_command() -> None:
        sys.stdout.write("契約\n")
        raise SystemExit(exit_code)

    monkeypatch.setattr(run_lint_imports.sys, "stdout", stdout)
    monkeypatch.setattr(run_lint_imports, "lint_imports_command", fake_command)

    assert run_lint_imports.main() == exit_code
    stdout.flush()
    assert raw.getvalue().decode("utf-8") == "契約\n"
