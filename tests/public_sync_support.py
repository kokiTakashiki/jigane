"""public_sync の試験が共有する補助。一時の git リポジトリで、公開の複製と書き出した木を組む。"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from pr_support import commit_all, git

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_FILES = {
    "pyproject.toml": '[project]\nversion = "0.1.7"\n',
    ".gitignore": ".venv/\n.ruff_cache/\ndist/\n",
    "keep.txt": "same\n",
    "changed.txt": "before\n",
    "old.txt": "removed in the tree\n",
    "sub/gone.txt": "removed in the tree\n",
}
TREE_FILES = {
    "pyproject.toml": '[project]\nversion = "0.1.8"\n',
    ".gitignore": ".venv/\n.ruff_cache/\ndist/\n",
    "keep.txt": "same\n",
    "changed.txt": "after\n",
    "new.txt": "added in the tree\n",
    ".venv/lib/x.py": "tree venv\n",
    ".ruff_cache/c": "tree cache\n",
    "dist/new.whl": "tree wheel\n",
}
IGNORED_IN_PUBLIC = {
    ".venv/lib/y.py": "public venv\n",
    ".ruff_cache/d": "public cache\n",
    "dist/old.whl": "old wheel\n",
}


def load_sync() -> ModuleType:
    path = ROOT / "scripts" / "public_sync.py"
    spec = importlib.util.spec_from_file_location("public_sync", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_files(base: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def build_world(tmp_path: Path) -> dict[str, Path]:
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    public = tmp_path / "public"
    git(tmp_path, "clone", "--no-local", str(origin), str(public))
    git(public, "switch", "-c", "main")
    write_files(public, PUBLIC_FILES)
    commit_all(public, "release")
    git(public, "push", "-u", "origin", "main")
    write_files(public, IGNORED_IN_PUBLIC)
    tree = tmp_path / "tree"
    write_files(tree, TREE_FILES)
    return {"origin": origin, "public": public, "tree": tree, "base": tmp_path}


def run_sync(
    world: dict[str, Path], *extra: str, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, str]:
    arguments = ["--tree", str(world["tree"]), "--public", str(world["public"]), *extra]
    code = load_sync().main(arguments)
    captured = capsys.readouterr()
    return code, captured.out, captured.err
