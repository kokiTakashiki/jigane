"""外への送り先を縛る静的な検査。jigane/ の木を構文木で読む。

1. ネットワークに触る部品の import と、tree_sitter_language_pack の取得の呼び出しが、
   scripts/egress_allowlist.toml の `site` の外に無いこと。
2. コードの中の文字列にあるホストと URL が、許可リストの中にあること（docstring は対象外）。
3. 起動する外部コマンドの先頭が git か、検査済みの gh と claude の口だけであること。
4. 許可リストの接続先と、docs/data-destinations.md の表「送り先」が一致すること。

scripts/ と tests/ は読まない。配布物に入らず、jigane の利用者の機械では走らないため。

使い方: check_egress.py [<根>]
"""

import ast
import re
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from markdown_it import MarkdownIt

ALLOWLIST_NAME = "scripts/egress_allowlist.toml"
DOCUMENT_NAME = "docs/data-destinations.md"
MODULE_SCOPE = "<module>"

NETWORK_MODULES = frozenset(
    {
        "urllib",
        "http",
        "socket",
        "ssl",
        "requests",
        "httpx",
        "aiohttp",
        "ftplib",
        "smtplib",
        "poplib",
        "imaplib",
        "telnetlib",
        "xmlrpc",
        "websocket",
        "websockets",
        "urllib3",
        "pycurl",
        "asyncio.streams",
        "asyncio.base_events",
        "socketserver",
        "tree_sitter_language_pack",
        "webbrowser",
        "smtpd",
        "asyncore",
        "asynchat",
        "nntplib",
        "multiprocessing.connection",
        "wsgiref",
        "anyio",
        "trio",
        "httpcore",
        "grpc",
        "paramiko",
        "boto3",
        "botocore",
        "ensurepip",
        "pip",
    }
)
ASYNCIO_MODULE = "asyncio"
# asyncio の直下にあって外へ繋ぐ名前。asyncio 全体を禁じると、タスクや待ち合わせまで落ちる。
ASYNCIO_NETWORK_NAMES = frozenset(
    {
        "streams",
        "base_events",
        "open_connection",
        "start_server",
        "open_unix_connection",
        "start_unix_server",
    }
)
# イベントループの方法。ループは変数越しに渡るので、持ち主を問わず名前で拾う。
LOOP_NETWORK_METHODS = frozenset(
    {
        "create_connection",
        "create_server",
        "create_datagram_endpoint",
        "create_unix_connection",
        "create_unix_server",
        "sock_connect",
        "getaddrinfo",
        "getnameinfo",
    }
)
PACK_MODULE = "tree_sitter_language_pack"
PACK_NETWORK_NAMES = frozenset(
    {
        "download",
        "download_all",
        "download_group",
        "prefetch",
        "manifest_languages",
        "manifest_groups",
        "init",
        "DownloadManager",
    }
)
DYNAMIC_IMPORTERS = frozenset({"__import__", "import_module"})

URL = re.compile(r"https?://[^\s\"'<>)\]\\]+")
# 文字列の中に埋まったドメイン名の形。ラベルが二つ以上で、最後が英字の TLD。
# 直前が英数字かハイフンなら語の途中、直前が `{` なら書式の欄（{item.value}）なので除く。
BARE_HOST = re.compile(
    r"(?<![A-Za-z0-9_{-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,24}(?![A-Za-z0-9_-])"
)
# ドメイン名の形でも、ファイル名の末尾として使われるもの。TLD と重なっても、
# 拾うとファイル名の誤検出が出るものだけを入れる。sh、us、xyz は TLD として拾うので入れない。
FILE_EXTENSIONS = frozenset(
    {
        "json", "toml", "yaml", "yml", "md", "txt", "lock", "cfg", "ini", "html", "css", "js",
        "ts", "csv", "log", "py", "pyi", "pyc", "so", "dll", "whl", "tar", "gz", "zip", "xml",
        "svg", "png", "jpg", "jpeg", "gif", "pdf", "rst", "tmp", "bak", "db", "sqlite", "jsonl",
        "ndjson", "scm", "tsx", "jsx", "mjs", "cjs", "env", "pem", "key", "sock", "pid",
        "cs", "java", "swift", "gradle", "kts", "config", "properties", "pbxproj", "lockfile",
        "diff", "exe", "props", "targets", "resolved", "kt", "go", "rs", "rb", "php",
        "tfstate",
    }
)  # fmt: skip
# 先頭のラベルがこれなら、ドメイン名ではなく属性や名前空間の参照（self.name、os.path）
NAMESPACE_ROOTS = frozenset({"self", "cls", "jigane"}) | frozenset(sys.stdlib_module_names)
LAUNCHERS = frozenset(
    {
        "run_command",
        "run_resolved",
        "run_claude_resolved",
    }
)
PROCESS_MODULE = "jigane.git.process"
# 起動の口の先頭に置いてよい実行ファイル。jigane.git.process の LAUNCHABLE_EXECUTABLES と
# 一致することを tests/test_claude_launch_definition.py が縛る
LAUNCHABLE_EXECUTABLES = frozenset({"git", "gh"})
# 起動の口の呼び出しの先頭が git の定数でなくてよい場所と、その場所が満たす条件
GH_MODULE = "jigane.pull_request_context.gh"
CLAUDE_MODULE = "jigane.llm_runs.claude"


def _module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _scopes(tree: ast.AST) -> Iterator[tuple[ast.AST, str]]:
    """全ての節を、それを囲む最も内側の関数の名前（無ければ <module>）と組にして返す。"""

    def walk(node: ast.AST, scope: str) -> Iterator[tuple[ast.AST, str]]:
        yield node, scope
        inner = node.name if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) else scope
        for child in ast.iter_child_nodes(node):
            yield from walk(child, inner)

    yield from walk(tree, MODULE_SCOPE)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def _is_network_module(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in NETWORK_MODULES)


def _pack_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """取得の部品を束ねた名前（import ... as）と、取得の関数そのものの名前（from ... import）。"""
    aliases: set[str] = set()
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(a.asname or a.name for a in node.names if a.name == PACK_MODULE)
        elif isinstance(node, ast.ImportFrom) and node.module == PACK_MODULE:
            functions.update(
                a.asname or a.name for a in node.names if a.name in PACK_NETWORK_NAMES
            )
    return aliases, functions


def _import_from_use(node: ast.ImportFrom, module: str) -> str | None:
    if _is_network_module(module):
        return f"ネットワークの部品 {module} の import"
    for alias in node.names:
        if _is_network_module(f"{module}.{alias.name}"):
            return f"ネットワークの部品 {module}.{alias.name} の import"
        if module == ASYNCIO_MODULE and alias.name in ASYNCIO_NETWORK_NAMES:
            return f"ネットワークの部品 asyncio の {alias.name} の import"
    return None


def _import_use(node: ast.AST) -> str | None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if _is_network_module(alias.name):
                return f"ネットワークの部品 {alias.name} の import"
    elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        return _import_from_use(node, node.module)
    elif isinstance(node, ast.Call):
        callee = node.func
        name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
        first = node.args[0] if node.args else None
        if (
            name in DYNAMIC_IMPORTERS
            and isinstance(first, ast.Constant)
            and isinstance(first.value, str)
            and _is_network_module(first.value)
        ):
            return f"ネットワークの部品 {first.value} の動的な import"
    return None


def _asyncio_aliases(tree: ast.AST) -> set[str]:
    return {
        a.asname or a.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for a in node.names
        if a.name == ASYNCIO_MODULE
    }


def _network_uses(tree: ast.AST) -> Iterator[tuple[ast.AST, str, str]]:
    """(節, 囲む関数, 説明) を返す。ネットワークに触る import と取得の呼び出し。"""
    aliases, functions = _pack_names(tree)
    asyncio_aliases = _asyncio_aliases(tree)
    for node, scope in _scopes(tree):
        what = _import_use(node)
        if what is not None:
            yield node, scope, what
        elif isinstance(node, ast.Attribute) and node.attr in LOOP_NETWORK_METHODS:
            yield node, scope, f"イベントループの {node.attr}（接続）"
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in asyncio_aliases
            and node.attr in ASYNCIO_NETWORK_NAMES
        ):
            yield node, scope, f"asyncio.{node.attr}（接続）"
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
            and node.attr in PACK_NETWORK_NAMES
        ):
            yield node, scope, f"{PACK_MODULE}.{node.attr}（取得）"
        elif isinstance(node, ast.Name) and node.id in functions:
            yield node, scope, f"{PACK_MODULE} の {node.id}（取得）"


def _load_allowlist(root: Path) -> dict[str, Any]:
    return tomllib.loads((root / ALLOWLIST_NAME).read_text(encoding="utf-8"))


def _line_number(node: ast.AST) -> int:
    # _scopes は節を AST の型で返す。報告に渡す節は import と参照で、どれも行を持つ
    if not isinstance(node, ast.expr | ast.stmt):
        message = f"行を持たない節は報告できない: {type(node).__name__}"
        raise TypeError(message)
    return node.lineno


def _reference_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def check_network_sites(root: Path, allowlist: dict[str, Any]) -> list[str]:
    sites = {(s["module"], s["scope"]) for s in allowlist.get("site", [])}
    problems = []
    for path in sorted((root / "jigane").rglob("*.py")):
        module = _module_name(root, path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node, scope, what in _network_uses(tree):
            if (module, scope) not in sites:
                problems.append(
                    f"{path.relative_to(root)}:{_line_number(node)}: {what}が許した場所の外にある"
                    f"（{module} の {scope}）。許すなら {ALLOWLIST_NAME} の site へ足す"
                )
    return problems


def _hosts_in(value: str) -> Iterator[str]:
    text = value.replace("\\.", ".")
    for match in URL.finditer(text):
        host = urlparse(match.group(0)).hostname
        if host:
            yield host
    # URL の中の道（…/claude-code.git）を裸のホストと取り違えないよう、URL は除いてから探す
    rest = URL.sub(" ", text)
    for match in BARE_HOST.finditer(rest):
        name = match.group(0)
        labels = name.lower().split(".")
        before = rest[: match.start()]
        after = rest[match.end() : match.end() + 1]
        if (
            name != name.lower()
            or after in ("=", "(")
            or labels[-1] in FILE_EXTENSIONS
            or labels[0] in NAMESPACE_ROOTS
            or (before.endswith("/") and not before.endswith("//"))
        ):
            continue  # 道、拡張子、名前空間の参照。ドメイン名として送り先にはならない
        yield name


def check_destinations(root: Path, allowlist: dict[str, Any]) -> list[str]:
    allowed = set(allowlist["destinations"]["hosts"]) | set(allowlist["display_only"]["hosts"])
    not_hosts = set(allowlist.get("not_hosts", {}).get("names", []))
    problems: list[str] = []
    for path in sorted((root / "jigane").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in docstrings:
                continue
            problems.extend(
                f"{path.relative_to(root)}:{node.lineno}: 許可リストに無い宛先 {host}。"
                f"許すなら {ALLOWLIST_NAME} へ足し、{DOCUMENT_NAME} の表も直す"
                for host in _hosts_in(node.value)
                if host not in allowed and host not in not_hosts
            )
    return problems


def _callee_name(call: ast.Call) -> str:
    callee = call.func
    return callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")


def _is_git_argv(node: ast.expr | None) -> bool:
    return (
        isinstance(node, ast.List)
        and bool(node.elts)
        and isinstance(node.elts[0], ast.Constant)
        and node.elts[0].value == "git"
    )


def _calls_named(tree: ast.AST, scope_name: str, called: str) -> bool:
    return any(
        isinstance(function, ast.FunctionDef)
        and function.name == scope_name
        and any(isinstance(n, ast.Call) and _callee_name(n) == called for n in ast.walk(function))
        for function in ast.walk(tree)
    )


def _claude_command_starts_with_binary(tree: ast.AST) -> bool:
    """build_command の組み立てる argv の先頭が claude_binary() で、その既定が "claude"。"""
    starts = False
    default = False
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        if function.name == "build_command":
            for node in ast.walk(function):
                if isinstance(node, ast.List) and node.elts:
                    head = node.elts[0]
                    starts = starts or (
                        isinstance(head, ast.Call) and _callee_name(head) == "claude_binary"
                    )
        elif function.name == "claude_binary":
            default = any(
                isinstance(n, ast.Constant) and n.value == "claude" for n in ast.walk(function)
            )
    return starts and default


def check_commands(root: Path) -> list[str]:
    """起動の口に渡る argv の先頭が、git の定数か、検査済みの gh と claude の口だけであること。

    subprocess と os.system の直接の利用は ruff の banned-api が禁じているので、ここでは見ない。
    gh の形の照合（check_allowed）と claude の実行ファイルの解決は、その口が担う。ここは、
    そこへ至る呼び出しが他の道を持たないことだけを見る。
    """
    problems = []
    for path in sorted((root / "jigane").rglob("*.py")):
        module = _module_name(root, path)
        if module == PROCESS_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        where = path.relative_to(root)
        callee_ids = set()
        for node, scope in _scopes(tree):
            if isinstance(node, ast.Call) and _callee_name(node) in LAUNCHERS:
                callee_ids.add(id(node.func))
                first = node.args[0] if node.args else None
                via_gh = (
                    module == GH_MODULE
                    and scope == "_run"
                    and _calls_named(tree, "_run", "check_allowed")
                )
                if not (_is_git_argv(first) or via_gh):
                    problems.append(
                        f"{where}:{node.lineno}: {_callee_name(node)} の先頭の引数が"
                        " git の定数でも、check_allowed を通る gh の口（gh.py の _run）でもない"
                    )
        for node in ast.walk(tree):
            reference = _reference_name(node)
            if reference in LAUNCHERS and id(node) not in callee_ids and module != CLAUDE_MODULE:
                problems.append(
                    f"{where}:{_line_number(node)}: 起動の口 {reference} を呼ばずに参照している。"
                    f"呼び出しなら先頭の引数を検査できる。claude の口（{CLAUDE_MODULE}）だけが例外"
                )
            elif isinstance(node, ast.ImportFrom) and module != CLAUDE_MODULE:
                problems.extend(
                    f"{where}:{node.lineno}: 起動の口 {alias.name} を import している。"
                    "import した名前は呼び出しの検査から外れる"
                    for alias in node.names
                    if alias.name in LAUNCHERS
                )
        if module == CLAUDE_MODULE and not _claude_command_starts_with_binary(tree):
            problems.append(
                f"{where}: build_command の argv の先頭が claude_binary() でない、"
                'または claude_binary の既定が "claude" でない'
            )
    return problems


def _destination_table_hosts(text: str) -> set[str] | None:
    """見出しに「送り先」を含む節の最初の表から、ホストの列のコードスパンを集める。

    ホストの列が全角の丸括弧で始まる行（claude を通す Anthropic、MCP サーバ）は除く。
    表が無ければ None。
    """
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    in_section = False
    in_head = False
    in_table = False
    hosts: set[str] = set()
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            in_section = "送り先" in tokens[index + 1].content
        elif not in_section:
            continue
        elif token.type == "table_open":
            in_table = True
        elif token.type == "table_close":
            break
        elif token.type == "thead_open":
            in_head = True
        elif token.type == "thead_close":
            in_head = False
        elif in_table and not in_head and token.type == "tr_open":
            # 各行の先頭のセルがホストの列
            first_cell = tokens[index + 2].content
            if not first_cell.startswith("（"):
                hosts.update(re.findall(r"`([^`]+)`", first_cell))
    return hosts if in_table else None


def check_document(root: Path, allowlist: dict[str, Any]) -> list[str]:
    path = root / DOCUMENT_NAME
    if not path.is_file():
        return [f"{DOCUMENT_NAME} が無い。許可リストの接続先と突き合わせられない"]
    documented = _destination_table_hosts(path.read_text(encoding="utf-8"))
    if documented is None:
        return [f"{DOCUMENT_NAME} に、見出しに「送り先」を含む節の表が無い"]
    allowed = set(allowlist["destinations"]["hosts"])
    return [
        f"{DOCUMENT_NAME} の表「送り先」にあり、{ALLOWLIST_NAME} の destinations に無い: {host}"
        for host in sorted(documented - allowed)
    ] + [
        f"{ALLOWLIST_NAME} の destinations にあり、{DOCUMENT_NAME} の表「送り先」に無い: {host}"
        for host in sorted(allowed - documented)
    ]


def main(argv: list[str]) -> int:
    root = Path(argv[0]).resolve() if argv else Path(__file__).resolve().parent.parent
    allowlist = _load_allowlist(root)
    problems = (
        check_network_sites(root, allowlist)
        + check_destinations(root, allowlist)
        + check_commands(root)
        + check_document(root, allowlist)
    )
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
