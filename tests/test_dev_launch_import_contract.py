import json
import os
import tomllib
from pathlib import Path

import pytest
from dev_launch import run_tool

from jigane.git.process import CommandError

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_NAME = "本体（jigane/）は開発の道具の起動口（scripts/dev_launch）を読まない"


def contract_toml() -> str:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "importlinter"
    ]
    contract = next(c for c in config["contracts"] if c["name"] == CONTRACT_NAME)
    # JSON の文字列、真偽値、配列は TOML のそれと同じ書き方になる。
    return (
        "[tool.importlinter]\n"
        'root_package = "jigane"\n'
        f"include_external_packages = {json.dumps(config['include_external_packages'])}\n"
        "[[tool.importlinter.contracts]]\n"
        f"name = {json.dumps(contract['name'], ensure_ascii=False)}\n"
        f"type = {json.dumps(contract['type'])}\n"
        f"source_modules = {json.dumps(contract['source_modules'])}\n"
        f"forbidden_modules = {json.dumps(contract['forbidden_modules'])}\n"
    )


def lint_imports(tmp_path: Path, source: str) -> str | None:
    """一時の木で契約を走らせ、落ちたときはその出力を返す。通れば None。"""
    (tmp_path / "pyproject.toml").write_text(contract_toml(), encoding="utf-8")
    for package in ("jigane", "scripts"):
        (tmp_path / package).mkdir(exist_ok=True)
        (tmp_path / package / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "dev_launch.py").write_text("", encoding="utf-8")
    (tmp_path / "dev_launch.py").write_text("", encoding="utf-8")
    (tmp_path / "jigane" / "sample.py").write_text(source, encoding="utf-8")
    try:
        run_tool(
            [
                "uv",
                "run",
                "--locked",
                "lint-imports",
                "--config",
                str(tmp_path / "pyproject.toml"),
            ],
            cwd=tmp_path,
            env={**os.environ, "UV_PROJECT": str(ROOT), "PYTHONPATH": str(tmp_path)},
        )
    except CommandError as error:
        return error.stdout + error.stderr
    return None


@pytest.mark.parametrize(
    "source",
    [
        "import dev_launch\n",
        "from dev_launch import run_tool\n",
        "from scripts import dev_launch\n",
        "import scripts.dev_launch\n",
        "import dev_launch as launch\n",
        "from scripts.dev_launch import run_tool\n",
    ],
)
def test_package_cannot_import_dev_launch(tmp_path: Path, source: str) -> None:
    output = lint_imports(tmp_path, source)
    assert output is not None
    assert CONTRACT_NAME in output
    assert "BROKEN" in output


def test_package_without_dev_launch_import_passes(tmp_path: Path) -> None:
    assert lint_imports(tmp_path, "import os\n") is None
