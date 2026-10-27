import importlib.util
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_checker() -> ModuleType:
    path = ROOT / "scripts" / "check_egress.py"
    spec = importlib.util.spec_from_file_location("check_egress", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ALLOWLIST = """
[destinations]
hosts = ["github.com"]
[display_only]
hosts = ["code.claude.com"]
[[site]]
module = "jigane.fetcher"
scope = "fetch"
"""


def make_tree(tmp_path: Path, sources: dict[str, str]) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "egress_allowlist.toml").write_text(ALLOWLIST, encoding="utf-8")
    (tmp_path / "jigane").mkdir()
    for name, source in sources.items():
        (tmp_path / "jigane" / name).write_text(textwrap.dedent(source), encoding="utf-8")
    return tmp_path


def run(root: Path) -> list[str]:
    checker = load_checker()
    allowlist = checker._load_allowlist(root)
    problems: list[str] = checker.check_network_sites(root, allowlist)
    problems += checker.check_destinations(root, allowlist)
    return problems


def test_real_tree_passes() -> None:
    assert run(ROOT) == []


def test_pack_download_in_allowed_function_passes(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        {
            "fetcher.py": """
            import tree_sitter_language_pack as pack

            def fetch():
                pack.download(["x"])
            """
        },
    )
    # fetcher.fetch は許した場所なので、呼び出しは落ちない。落ちるのは <module> の import だけ
    assert all("<module>" in p for p in run(root))


def test_import_outside_the_allowed_function_of_the_same_file_fails(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        {
            "fetcher.py": """
            import tree_sitter_language_pack as pack

            def fetch():
                pack.download(["x"])
            """
        },
    )
    # 許したのは fetch だけで、同じファイルの <module> にある import は許していない
    problems = run(root)
    assert len(problems) == 1
    assert "jigane/fetcher.py:2" in problems[0]
    assert "<module>" in problems[0]


def test_network_import_outside_allowed_site_fails(tmp_path: Path) -> None:
    root = make_tree(tmp_path, {"a.py": "import urllib.request\n"})
    problems = run(root)
    assert len(problems) == 1
    assert "jigane/a.py:1" in problems[0]
    assert "urllib.request" in problems[0]


def test_pack_download_outside_allowed_site_fails(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        {
            "a.py": """
            from tree_sitter_language_pack import download

            def other():
                download(["x"])
            """
        },
    )
    assert any("jigane/a.py:2" in p for p in run(root))
    assert any("jigane/a.py:5" in p for p in run(root))


def test_pack_attribute_download_in_wrong_function_fails(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        {
            "fetcher.py": """
            import tree_sitter_language_pack as pack

            def other():
                pack.download(["x"])
            """
        },
    )
    assert any("jigane/fetcher.py:5" in p and "other" in p for p in run(root))


def test_dynamic_import_of_network_module_fails(tmp_path: Path) -> None:
    root = make_tree(tmp_path, {"a.py": 'import importlib\nimportlib.import_module("socket")\n'})
    assert any("jigane/a.py:2" in p for p in run(root))


def test_url_not_in_allowlist_fails(tmp_path: Path) -> None:
    root = make_tree(tmp_path, {"a.py": 'URL = "https://example.com/x"\n'})
    problems = run(root)
    where = "jigane/a.py:1: 許可リストに無い宛先 example.com。"
    how = "許すなら scripts/egress_allowlist.toml へ足し、docs/data-destinations.md の表も直す"
    assert problems == [where + how]


def test_bare_host_and_escaped_pattern_are_checked(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        {"a.py": 'HOST = "evil.io"\nPATTERN = r"^https://evil\\.net/(.+)$"\n'},
    )
    hosts = sorted(p.split("宛先 ")[1].split("。")[0] for p in run(root))
    assert hosts == ["evil.io", "evil.net"]


def test_allowed_and_display_only_hosts_pass(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        {"a.py": 'A = "https://github.com/x"\nB = "https://code.claude.com/d"\nC = "README.md"\n'},
    )
    assert run(root) == []


def test_docstring_urls_are_not_checked(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        {"a.py": '"""https://example.com/doc"""\n\ndef f():\n    """http://example.org"""\n'},
    )
    assert run(root) == []


def test_files_outside_jigane_are_not_read(tmp_path: Path) -> None:
    root = make_tree(tmp_path, {})
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text(
        'import socket\nURL = "https://example.com/x"\n', encoding="utf-8"
    )
    assert run(root) == []


@pytest.mark.parametrize(
    "source",
    [
        "from asyncio import streams\n",
        "from asyncio import open_connection\n",
        "from asyncio.streams import open_connection\n",
        "import asyncio\nasyncio.open_connection('h', 1)\n",
        "import asyncio as aio\naio.start_server(None)\n",
        "import asyncio\nasyncio.streams.open_connection('h', 1)\n",
        "import asyncio\nloop = asyncio.get_running_loop()\nloop.create_connection(None)\n",
        "from http import client\n",
        "import http.client as h\n",
        "from multiprocessing import connection\n",
        "import webbrowser\n",
        "from xmlrpc import client\n",
        "import importlib\nimportlib.import_module('webbrowser')\n",
    ],
)
def test_other_forms_of_network_access_fail(tmp_path: Path, source: str) -> None:
    root = make_tree(tmp_path, {"a.py": source})
    assert any("jigane/a.py" in p for p in run(root))


def test_asyncio_without_network_passes(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        {"a.py": "import asyncio\nfrom asyncio import gather, Queue\nasyncio.sleep(1)\n"},
    )
    assert run(root) == []


@pytest.mark.parametrize(
    ("text", "host"),
    [
        ("connect to example.xyz now", "example.xyz"),
        ("evil.sh", "evil.sh"),
        ("mirror.example.us/path", "mirror.example.us"),
        ("user@mail.example.cloud", "mail.example.cloud"),
        ("{sub}.example.dev", "example.dev"),
        ("see https://evil.io/a.json", "evil.io"),
    ],
)
def test_embedded_and_unlisted_tld_hosts_fail(tmp_path: Path, text: str, host: str) -> None:
    root = make_tree(tmp_path, {"a.py": f"X = {text!r}\n"})
    assert [p.split("宛先 ")[1].split("。")[0] for p in run(root)] == [host]


@pytest.mark.parametrize(
    "text",
    [
        "review.json",
        "pyproject.toml",
        "jigane.storage",
        "os.path",
        "self.name",
        "src/jigane/review.sh.md",
        "path/claude-code.git",
        "Task.Factory",
        "expected.total_usd",
        "core.quotepath=off",
        "{item.value}",
        "e.g. this",
        "version 1.2.3",
        "see https://github.com/x/claude-code.git",
    ],
)
def test_file_names_and_namespaces_are_not_hosts(tmp_path: Path, text: str) -> None:
    root = make_tree(tmp_path, {"a.py": f"X = {text!r}\n"})
    assert run(root) == []


def test_not_hosts_list_exempts_named_strings(tmp_path: Path) -> None:
    root = make_tree(tmp_path, {"a.py": 'X = "java.awt"\n'})
    assert any("java.awt" in p for p in run(root))
    checker = load_checker()
    allowlist = checker._load_allowlist(root)
    allowlist["not_hosts"] = {"names": ["java.awt"]}
    assert checker.check_destinations(root, allowlist) == []


def commands(root: Path) -> list[str]:
    problems: list[str] = load_checker().check_commands(root)
    return problems


def test_real_tree_commands_pass() -> None:
    assert commands(ROOT) == []


def test_git_literal_launch_passes(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        {
            "a.py": """
            from jigane.git import process

            def f():
                process.run_command(["git", "status"])
            """
        },
    )
    assert commands(root) == []


def test_other_command_launch_fails(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        {
            "a.py": """
            from jigane.git import process

            def f():
                process.run_command(["curl", "https://x"])
            """
        },
    )
    problems = commands(root)
    assert len(problems) == 1
    assert "jigane/a.py:5" in problems[0]


def test_variable_argv_launch_fails(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        {
            "a.py": """
            from jigane.git import process

            def f(argv):
                process.run_command(argv)
            """
        },
    )
    assert any("jigane/a.py:5" in p for p in commands(root))


def test_launcher_imported_by_name_fails(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        {"a.py": "from jigane.git.process import run_command\n"},
    )
    assert any("jigane/a.py:1" in p for p in commands(root))


def test_launcher_passed_as_value_fails(tmp_path: Path) -> None:
    root = make_tree(
        tmp_path,
        {"a.py": "from jigane.git import process\n\nrunner = process.run_resolved\n"},
    )
    assert any("jigane/a.py:3" in p for p in commands(root))


def test_gh_launch_without_check_allowed_fails(tmp_path: Path) -> None:
    root = make_tree(tmp_path, {})
    (root / "jigane" / "pull_request_context").mkdir()
    (root / "jigane" / "pull_request_context" / "gh.py").write_text(
        "from jigane.git import process\n\ndef _run(argv):\n"
        "    return process.run_command(argv)\n",
        encoding="utf-8",
    )
    assert any("gh.py:4" in p for p in commands(root))


def test_claude_binary_head_changed_fails(tmp_path: Path) -> None:
    root = make_tree(tmp_path, {})
    (root / "jigane" / "llm_runs").mkdir()
    (root / "jigane" / "llm_runs" / "claude.py").write_text(
        'def build_command(invocation):\n    return ["curl"]\n\n'
        'def claude_binary():\n    return "claude"\n',
        encoding="utf-8",
    )
    assert any("build_command" in p for p in commands(root))


DOCUMENT = """# データの送り先

## 送り先

| ホスト | 送るもの | 時機 | 口 |
| --- | --- | --- | --- |
| `github.com` | 取得の要求 | `new pr` | gh |
| `api.github.com`、`release-assets.githubusercontent.com` | 取得の要求 | `new pr` | gh |
| （claude を通して） | レビューの文面 | `review` | claude |
| （利用者が選ぶ MCP サーバ） | 選んだ分 | `--mcps` | claude |

## 次の節

| `other.example` | 無関係の表 |
"""
DOCUMENT_ALLOWLIST = {
    "destinations": {
        "hosts": ["github.com", "api.github.com", "release-assets.githubusercontent.com"]
    }
}


def write_document(root: Path, text: str) -> None:
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "data-destinations.md").write_text(text, encoding="utf-8")


def document(root: Path, allowlist: dict[str, Any]) -> list[str]:
    problems: list[str] = load_checker().check_document(root, allowlist)
    return problems


def test_real_document_matches_allowlist() -> None:
    allowlist = load_checker()._load_allowlist(ROOT)
    assert document(ROOT, allowlist) == []


def test_document_matching_allowlist_passes(tmp_path: Path) -> None:
    write_document(tmp_path, DOCUMENT)
    assert document(tmp_path, DOCUMENT_ALLOWLIST) == []


def test_host_only_in_document_fails(tmp_path: Path) -> None:
    write_document(tmp_path, DOCUMENT.replace("`github.com` |", "`github.com`、`evil.example` |"))
    problems = document(tmp_path, DOCUMENT_ALLOWLIST)
    where = "docs/data-destinations.md の表「送り先」にあり、"
    what = "scripts/egress_allowlist.toml の destinations に無い: evil.example"
    assert problems == [where + what]


def test_host_only_in_allowlist_fails(tmp_path: Path) -> None:
    write_document(tmp_path, DOCUMENT)
    allowlist = {"destinations": {"hosts": [*DOCUMENT_ALLOWLIST["destinations"]["hosts"], "x.io"]}}
    problems = document(tmp_path, allowlist)
    where = "scripts/egress_allowlist.toml の destinations にあり、"
    what = "docs/data-destinations.md の表「送り先」に無い: x.io"
    assert problems == [where + what]


def test_missing_document_fails(tmp_path: Path) -> None:
    assert any("が無い" in p for p in document(tmp_path, DOCUMENT_ALLOWLIST))


def test_document_without_table_fails(tmp_path: Path) -> None:
    write_document(tmp_path, "# 送り先\n\n表なし\n")
    assert any("表が無い" in p for p in document(tmp_path, DOCUMENT_ALLOWLIST))
