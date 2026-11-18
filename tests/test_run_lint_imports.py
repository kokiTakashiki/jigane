import io
import sys
from types import ModuleType

import pytest
from script_loader import import_script


def load_script() -> ModuleType:
    return import_script("run_lint_imports")


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
