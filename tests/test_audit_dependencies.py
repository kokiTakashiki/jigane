import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "audit_dependencies", ROOT / "scripts" / "audit_dependencies.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record_runs(monkeypatch: pytest.MonkeyPatch, audit_code: int) -> tuple[list[list[str]], int]:
    module = load_module()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, audit_code if command[1] == "run" else 0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    return calls, module.main()


def test_export_and_audit_arguments_are_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, _ = record_runs(monkeypatch, 0)
    export, audit = calls
    requirements = export[export.index("-o") + 1]
    assert export == [
        "uv", "export", "--locked", "--format", "requirements-txt", "--no-header", "--quiet",
        "--no-emit-project", "-o", requirements, "--no-emit-package", "jigane-language-kotlin",
    ]  # fmt: skip
    assert audit == [
        "uv", "run", "--locked", "pip-audit", "--strict", "--disable-pip", "--no-deps",
        "-r", requirements,
    ]  # fmt: skip


def test_found_vulnerability_makes_the_script_exit_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, code = record_runs(monkeypatch, 1)
    assert code == 1


def test_failed_export_ends_with_its_exit_code_without_running_the_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 2)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.main() == 2
    assert [command[1] for command in calls] == ["export"]
