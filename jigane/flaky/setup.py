"""共通のセットアップ（conftest.py と autouse の fixture）が固定する時刻と種を、静的に拾う。"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from tree_sitter import Node

from jigane.git.tree_files import read_tree_bytes
from jigane.languages.base import LanguageAdapter, parse
from jigane.preconditions.required import required

_FIXTURE_NAMES = frozenset({"pytest.fixture", "fixture"})
TIME_FIXATIONS = frozenset(
    {"freeze_time", "freezegun.freeze_time", "time_machine.travel", "travel"}
)


@dataclass(frozen=True)
class Fixation:
    """時刻か乱数の種を固定している呼び出しの場所。"""

    path: str
    line: int


@dataclass(frozen=True)
class FileContext:
    """検査の対象のファイルの外で見えた、共通のセットアップの情報。"""

    time_fixations: tuple[Fixation, ...] = ()
    seed_fixations: tuple[Fixation, ...] = ()

    def merged(self, other: "FileContext") -> "FileContext":
        return FileContext(
            (*self.time_fixations, *other.time_fixations),
            (*self.seed_fixations, *other.seed_fixations),
        )


def _text(node: Node) -> str:
    return required(node.text).decode()


def _is_autouse_fixture(decorator: Node) -> bool:
    call = next((c for c in decorator.named_children if c.type == "call"), None)
    if call is None or _text(required(call.child_by_field_name("function"))) not in _FIXTURE_NAMES:
        return False
    arguments = required(call.child_by_field_name("arguments"))
    for argument in arguments.named_children:
        if argument.type != "keyword_argument":
            continue
        name = required(argument.child_by_field_name("name"))
        value = required(argument.child_by_field_name("value"))
        if _text(name) == "autouse" and value.type == "true":
            return True
    return False


def in_autouse_fixture(node: Node) -> bool:
    """node が、トップレベルの autouse の fixture の内側にあるか。"""
    current = node.parent
    while current is not None:
        if current.type == "decorated_definition" and any(
            c.type == "decorator" and _is_autouse_fixture(c) for c in current.children
        ):
            return True
        current = current.parent
    return False


def _calls(node: Node) -> list[Node]:
    found = [node] if node.type == "call" else []
    for child in node.children:
        found.extend(_calls(child))
    return found


def _segments(node: Node) -> list[str]:
    """第一引数の字面を、ドットで区切った語にする。文字列の引数は中身を使う。"""
    if node.type == "string":
        content = next((c for c in node.named_children if c.type == "string_content"), None)
        return _text(content).split(".") if content is not None else []
    if node.type in ("identifier", "attribute"):
        return _text(node).split(".")
    return []


def _patch_kind(call: Node) -> str | None:
    """monkeypatch.setattr の第一引数が字面で指す対象。"time"、"seed"、判断できなければ None。"""
    arguments = call.child_by_field_name("arguments")
    if arguments is None or not arguments.named_children:
        return None
    segments = _segments(arguments.named_children[0])
    if "random" in segments:
        return "seed"
    # time.sleep の差し替えは時刻を固定しない
    if ("datetime" in segments or "time" in segments) and segments[-1] != "sleep":
        return "time"
    return None


def _autouse_fixture_bodies(root: Node) -> Iterator[Node]:
    """トップレベルの autouse の fixture それぞれの、関数の本体。"""
    for node in root.children:
        if node.type != "decorated_definition":
            continue
        if not any(c.type == "decorator" and _is_autouse_fixture(c) for c in node.children):
            continue
        body = required(node.child_by_field_name("definition")).child_by_field_name("body")
        if body is None:
            continue
        yield body


def _fixation_kind(call: Node) -> str | None:
    """call が固定する対象。"time"、"seed"、固定の呼び出しでなければ None。"""
    name = _text(required(call.child_by_field_name("function")))
    if name in TIME_FIXATIONS:
        return "time"
    if name == "random.seed":
        return "seed"
    if name == "monkeypatch.setattr":
        return _patch_kind(call)
    return None


def fixations_in(path: str, source: bytes, adapter: LanguageAdapter) -> FileContext:
    """source の autouse の fixture の本体にある、時刻と乱数の種を固定する呼び出しを返す。"""
    root = parse(adapter, source).root_node
    times: list[Fixation] = []
    seeds: list[Fixation] = []
    for body in _autouse_fixture_bodies(root):
        for call in _calls(body):
            fixation = Fixation(path, call.start_point.row + 1)
            kind = _fixation_kind(call)
            if kind == "time":
                times.append(fixation)
            elif kind == "seed":
                seeds.append(fixation)
    return FileContext(tuple(times), tuple(seeds))


def conftest_paths(path: str) -> list[str]:
    """テストファイルと同じディレクトリから木の根までの、各階層の conftest.py の相対パス。"""
    directory = PurePosixPath(path).parent
    chain = [directory, *directory.parents]
    return [(d / "conftest.py").as_posix() for d in chain]


def conftest_context(root: Path, path: str, adapter: LanguageAdapter) -> FileContext:
    """テストファイルに効く conftest.py の階層を読み、固定を集める。"""
    context = FileContext()
    for conftest in conftest_paths(path):
        source = read_tree_bytes(root, conftest)
        if source is not None:
            context = context.merged(fixations_in(conftest, source, adapter))
    return context
