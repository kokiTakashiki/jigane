"""設計書の名指しをいまのコードに引き当て、突き合わせるファイルを推す。設計は 4.5 節。

LLM は呼ばない。同じ設計書と同じ作業ディレクトリからは、同じ推しと同じ照合の表が出る。
言語アダプタの無いファイルは索引に入らないので、型と呼び出しの引き当てには現れない。
パスの名指しだけは、試験を除く全てのファイルを引き当てる。
"""

import ast
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import ClassVar, assert_never

from jigane.design.mentions import (
    PROSE_VIAS,
    STORED_FILE_SUFFIXES,
    Mention,
    MentionKind,
    extract_mentions,
    section_key,
)
from jigane.design.parse import DesignDocument
from jigane.git.tree_files import read_tree_bytes, work_files
from jigane.judgment_map.classification import matches_any
from jigane.judgment_map.configuration import load_config
from jigane.languages import registry
from jigane.sequence.index import Member, TypeInfo, TypeKey
from jigane.sequence.indexes import Indexes
from jigane.sequence.resolve import Resolved, extract_calls

_MIN_LOCATIONS = 2

DEFAULT_LIMIT = 8


class CandidateKind(StrEnum):
    DIRECT = "direct"
    ONE_STEP = "one_step"
    TWO_STEP = "two_step"

    @property
    def depth(self) -> int:
        """名指しから何歩目か。候補の種類は、理由の歩数の最小から決まる。"""
        match self:
            case CandidateKind.DIRECT:
                return 0
            case CandidateKind.ONE_STEP:
                return 1
            case CandidateKind.TWO_STEP:
                return 2
            case _ as unreachable:
                assert_never(unreachable)


class Trace(StrEnum):
    """候補が名指しにつながる道。NAMED は名指しが直接引き当たったもの。"""

    NAMED = "named"
    CALLER = "caller"
    CALLEE = "callee"
    # 保存物の名前を持つ定数を参照する関数のあるファイル。関数の中に書き込みの呼び出しが
    # 無ければ読む側、あれば書く側。一つのファイルが両方を持ちうる
    READER = "reader"
    WRITER = "writer"
    # READER か WRITER の関数を呼ぶファイル。名指しからは二段目
    ACCESSOR_CALLER = "accessor_caller"

    @property
    def step(self) -> CandidateKind:
        """この道が名指しから何歩目の根拠か。"""
        match self:
            case Trace.NAMED:
                return CandidateKind.DIRECT
            case Trace.CALLER | Trace.CALLEE | Trace.READER | Trace.WRITER:
                return CandidateKind.ONE_STEP
            case Trace.ACCESSOR_CALLER:
                return CandidateKind.TWO_STEP
            case _ as unreachable:
                assert_never(unreachable)


class LocationKind(StrEnum):
    """引き当てた先の種類の唯一の定義。行を持つかどうかも種類から決まる。"""

    # 型の宣言（TypeKind と値が揃う）
    CLASS = "class"
    STRUCT = "struct"
    RECORD = "record"
    INTERFACE = "interface"
    ENUM = "enum"
    DELEGATE = "delegate"
    MODULE = "module"
    # 型の中、またはモジュール直下の宣言（MemberKind と値が揃う。FUNCTION はモジュール直下の関数）
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    FIELD = "field"
    PROPERTY = "property"
    PARAMETER = "parameter"
    VARIABLE = "variable"
    # 宣言以外
    FILE = "file"
    DIRECTORY = "directory"
    SUBCOMMAND = "subcommand"
    OPTION = "option"
    STORED_FILE = "stored_file"

    @property
    def has_line(self) -> bool:
        match self:
            case LocationKind.FILE | LocationKind.DIRECTORY:
                return False
            case (
                LocationKind.CLASS
                | LocationKind.STRUCT
                | LocationKind.RECORD
                | LocationKind.INTERFACE
                | LocationKind.ENUM
                | LocationKind.DELEGATE
                | LocationKind.MODULE
                | LocationKind.FUNCTION
                | LocationKind.METHOD
                | LocationKind.CONSTRUCTOR
                | LocationKind.FIELD
                | LocationKind.PROPERTY
                | LocationKind.PARAMETER
                | LocationKind.VARIABLE
                | LocationKind.SUBCOMMAND
                | LocationKind.OPTION
                | LocationKind.STORED_FILE
            ):
                return True
            case _ as unreachable:
                assert_never(unreachable)


@dataclass(frozen=True)
class Location:
    path: str
    line: int | None
    label: str
    kind: LocationKind
    # 追跡していないファイルの場所。決めるのは _World がファイルを列挙する一か所
    untracked: bool = False

    def __post_init__(self) -> None:
        if self.kind.has_line != (self.line is not None):
            raise ValueError(f"{self.kind.value} の場所の行が種類と合いません: {self.line}")


@dataclass(frozen=True)
class Found:
    mention: Mention
    # 一つ以上ある
    locations: tuple[Location, ...]

    def __post_init__(self) -> None:
        if not self.locations:
            raise ValueError(f"引き当てた場所がありません: {self.mention.name}")


@dataclass(frozen=True)
class NotFound:
    mention: Mention


@dataclass(frozen=True)
class Ambiguous:
    """名前は引けたが、候補が複数あって一つに絞れなかったもの。推しの候補には入れない。"""

    mention: Mention
    # 二つ以上ある
    locations: tuple[Location, ...]

    def __post_init__(self) -> None:
        if len(self.locations) < _MIN_LOCATIONS:
            raise ValueError(f"曖昧とする候補が二つ未満です: {self.mention.name}")


Match = Found | NotFound | Ambiguous


@dataclass(frozen=True)
class NamedReason:
    """名指しが直接引き当たった。"""

    mention: Mention
    trace: ClassVar[Trace] = Trace.NAMED


@dataclass(frozen=True)
class CallerReason:
    """名指しの宣言を呼ぶ側。"""

    mention: Mention
    trace: ClassVar[Trace] = Trace.CALLER


@dataclass(frozen=True)
class CalleeReason:
    """名指しの宣言が呼ぶ先。"""

    mention: Mention
    trace: ClassVar[Trace] = Trace.CALLEE


@dataclass(frozen=True)
class ReaderReason:
    """保存物の定数を参照して読む関数 `function` を持つ。"""

    mention: Mention
    function: str
    trace: ClassVar[Trace] = Trace.READER


@dataclass(frozen=True)
class WriterReason:
    """保存物の定数を参照して書く関数 `function` を持つ。"""

    mention: Mention
    function: str
    trace: ClassVar[Trace] = Trace.WRITER


@dataclass(frozen=True)
class AccessorCallerReason:
    """保存物を読み書きする関数 `function` を呼ぶ。"""

    mention: Mention
    function: str
    trace: ClassVar[Trace] = Trace.ACCESSOR_CALLER


# 候補になった理由。どの名指しが、どの道で候補につながったか。道の種類ごとに型を分け、
# 関数名はそれを持つ道の型だけが必須の項目として持つ
Reason = (
    NamedReason | CallerReason | CalleeReason | ReaderReason | WriterReason | AccessorCallerReason
)


@dataclass(frozen=True)
class Candidate:
    path: str
    # 直接の引き当ての理由を先に置く。直接でもある候補には、たどりの理由が後ろに付く
    reasons: tuple[Reason, ...]
    # 追跡していないファイル。決めるのは _World がファイルを列挙する一か所
    untracked: bool = False

    @property
    def kind(self) -> CandidateKind:
        return min((r.trace.step for r in self.reasons), key=lambda step: step.depth)

    @property
    def section_count(self) -> int:
        # 直接の候補は、このファイルに引き当たった名指しの節の数。
        # 一段のたどりの候補は、たどりの起点になった名指しの節の数
        named = self.kind is CandidateKind.DIRECT
        return len(
            {
                section_key(r.mention)
                for r in self.reasons
                if (r.trace.step is CandidateKind.DIRECT) == named
            }
        )


@dataclass(frozen=True)
class Recommendation:
    candidates: tuple[Candidate, ...]
    # 上限で切った候補の数
    omitted: int
    matches: tuple[Match, ...]


_ADD_PARSER = re.compile(r"""add_parser\(\s*["']([^"']+)["']""")
_CONSTANT_DECLARATION = re.compile(r"""^\s*([A-Za-z_]\w*)\s*(?::[^=]+)?=\s*["']""")
_CONSTANT_VALUE = re.compile(r"""^\s*[A-Za-z_]\w*\s*(?::[^=]+)?=\s*["']([^"']+)["']""")
_TEXT_LIMIT_BYTES = 1_000_000
# 設計書に付属する検証用の一時スクリプトなど、製品ではないソースの置き場
_NON_PRODUCT_DIRECTORY = "docs"


class _Tally:
    """候補のパスごとの理由。直接の理由を先に、たどりの理由を後ろに並べる。"""

    def __init__(self) -> None:
        self._direct: list[Reason] = []
        self._trace: list[Reason] = []

    def add(self, reason: Reason) -> None:
        target = self._direct if reason.trace.step is CandidateKind.DIRECT else self._trace
        if reason not in target:
            target.append(reason)

    def reasons(self) -> tuple[Reason, ...]:
        return (*self._direct, *self._trace)


@dataclass(frozen=True)
class _Declaration:
    type_info: TypeInfo | None
    member: Member | None


@dataclass
class _Edges:
    # 呼び元の (パス, 行) から、呼び先のファイル。同じファイルの中の呼び出しは含めない
    callees: dict[tuple[str, int], set[str]] = field(default_factory=lambda: defaultdict(set))
    callers_of_member: dict[tuple[str, int], set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    callers_of_type: dict[TypeKey, set[str]] = field(default_factory=lambda: defaultdict(set))


class _World:
    """作業ディレクトリの全ファイルと、必要になったときに作る索引と呼び出しの辺。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        adapters = registry.adapters()
        excluded = load_config(root).exclude
        # 試験のファイルは、設計書が名指す先にも、呼び手の一段のたどりにも入れない
        work = work_files(root, include_untracked=True)
        files, self._untracked = work.files, work.untracked
        self.paths = tuple(p for p in sorted(files) if not registry.is_test_path(p, adapters))
        # 索引と引き当ての対象にするソース。設定の除外と、docs の下のソースは製品ではない
        self.source_paths = tuple(
            p
            for p in self.paths
            if registry.adapter_for(p) is not None
            and not matches_any(p, excluded)
            and PurePosixPath(p).parts[0] != _NON_PRODUCT_DIRECTORY
        )
        self._indexes: Indexes | None = None
        self._edges: _Edges | None = None
        self._texts: dict[str, str] = {}

    def is_untracked(self, path: str) -> bool:
        return path in self._untracked

    @property
    def indexes(self) -> Indexes:
        if self._indexes is None:
            self._indexes = Indexes(self.root, self.source_paths)
        return self._indexes

    def text(self, path: str) -> str:
        if path not in self._texts:
            data = read_tree_bytes(self.root, path)
            readable = data is not None and len(data) <= _TEXT_LIMIT_BYTES
            self._texts[path] = data.decode("utf-8", errors="replace") if readable and data else ""
        return self._texts[path]

    @property
    def edges(self) -> _Edges:
        if self._edges is None:
            self._edges = self._build_edges()
        return self._edges

    def _build_edges(self) -> _Edges:
        edges = _Edges()
        for index in self.indexes.indexes:
            for caller in index.callables.values():
                for call in extract_calls(index, caller).calls:
                    resolution = call.resolution
                    if not isinstance(resolution, Resolved):
                        continue
                    member = resolution.member
                    owner = index.types.get(resolution.type)
                    target = member.path if member is not None else owner.path if owner else None
                    if target is None or target == caller.path:
                        # 同じファイルの中の呼び出しは、新しいファイルを連れてこない
                        continue
                    edges.callees[(caller.path, caller.line)].add(target)
                    edges.callers_of_type[resolution.type].add(caller.path)
                    if member is not None:
                        edges.callers_of_member[(member.path, member.line)].add(caller.path)
                        edges.callers_of_type[member.owner].add(caller.path)
        return edges


def recommend(root: Path, document: DesignDocument, limit: int = DEFAULT_LIMIT) -> Recommendation:
    """設計書の名指しをいまのコードに引き当て、突き合わせるファイルを上限つきで推す。"""
    if limit < 1:
        raise ValueError(f"limit は 1 以上にしてください: {limit}")
    world = _World(root.resolve())
    mentions = extract_mentions(
        document,
        _stored_constant_values(world),
        frozenset(p.rsplit(".", 1)[-1] for p in world.paths if "." in PurePosixPath(p).name),
    )
    tallies: dict[str, _Tally] = defaultdict(_Tally)
    matches: list[Match] = []
    commands = _commands(world)
    commands_by_section = _command_files(mentions, commands)
    types_by_section = _section_types(world, mentions)
    document_types = _document_types(types_by_section)

    for mention in mentions:
        located = _locate(
            world,
            mention,
            commands,
            commands_by_section,
            types_by_section,
            document_types=document_types,
        )
        if located is None:
            continue
        locations, declarations, ambiguous = located
        if not locations and ambiguous:
            matches.append(Ambiguous(mention, tuple(ambiguous)))
            continue
        if not locations:
            matches.append(NotFound(mention))
            continue
        matches.append(Found(mention, tuple(locations)))
        _credit(world, mention, locations, declarations, tallies)

    candidates = _ranked(world, tallies)
    return Recommendation(candidates[:limit], max(0, len(candidates) - limit), tuple(matches))


def _locate(
    world: _World,
    mention: Mention,
    commands: dict[str, list[Location]],
    commands_by_section: dict[int, list[str]],
    types_by_section: dict[int, list[TypeInfo]],
    *,
    document_types: list[TypeInfo],
) -> tuple[list[Location], list[_Declaration], list[Location]] | None:
    """名指しの引き当て先を (場所, たどりの起点, 曖昧な場所) で返す。表に出さない名指しは None。"""
    declarations: list[_Declaration] = []
    match mention.kind:
        case MentionKind.TYPE | MentionKind.QUALIFIED_NAME:
            return _declarations(
                world,
                mention,
                types_by_section.get(section_key(mention), []),
                document_types,
            )
        case MentionKind.PATH:
            locations = _existing_paths(world, mention.name)
            if mention.via in PROSE_VIAS and not locations:
                # 地の文の語はパスの形をしているだけかもしれない。実在しなければ表に出さない
                return None
        case MentionKind.SUBCOMMAND:
            locations = commands.get(mention.name, [])
        case MentionKind.OPTION:
            scoped = commands_by_section.get(section_key(mention), [])
            locations = _quoted_in(
                world, mention.name, scoped or _cli_paths(world), LocationKind.OPTION
            )
        case MentionKind.STORED_FILE:
            locations = _quoted_in(
                world,
                mention.name,
                world.source_paths,
                LocationKind.STORED_FILE,
                prefixed=True,
            )
    return locations, declarations, []


def _credit(
    world: _World,
    mention: Mention,
    locations: list[Location],
    declarations: list[_Declaration],
    tallies: dict[str, _Tally],
) -> None:
    """引き当てた名指しの理由を、ファイルごとの集計に足す。"""
    reason = NamedReason(mention)
    for path in sorted({loc.path for loc in locations if not loc.path.endswith("/")}):
        tallies[path].add(reason)
    if mention.kind is MentionKind.STORED_FILE:
        accessors = _stored_accessors(world, mention.name, locations)
        for accessor in accessors:
            tallies[accessor.path].add(
                WriterReason(mention, accessor.function)
                if accessor.writes
                else ReaderReason(mention, accessor.function)
            )
        for accessor in accessors:
            callers = world.edges.callers_of_member.get((accessor.path, accessor.line), set())
            for path in sorted(callers):
                tallies[path].add(AccessorCallerReason(mention, accessor.function))
    for declaration in declarations:
        _trace(world, declaration, mention, tallies)


def _ranked(world: _World, tallies: dict[str, _Tally]) -> tuple[Candidate, ...]:
    built = [
        Candidate(path, tally.reasons(), world.is_untracked(path))
        for path, tally in tallies.items()
    ]
    # 名指した節の数の多い順、そのうえで直接の引き当て、一段、二段のたどりの順、同点はパスの順
    rank = {CandidateKind.DIRECT: 0, CandidateKind.ONE_STEP: 1, CandidateKind.TWO_STEP: 2}
    built.sort(key=lambda c: (-c.section_count, rank[c.kind], c.path))
    return tuple(built)


def _suffix_match(qualified: str, name: str) -> bool:
    return qualified == name or qualified.endswith("." + name)


def _class_like_types(world: _World, name: str) -> list[TypeInfo]:
    """名前に一致する、モジュールでない型。"""
    return [
        info
        for index in world.indexes.indexes
        for info in index.types.values()
        if _suffix_match(info.qualified_name, name) and info.kind != "module"
    ]


def _section_types(world: _World, mentions: tuple[Mention, ...]) -> dict[int, list[TypeInfo]]:
    """節ごとに、その節が名指した型。単独名の絞りの手がかりになる。"""
    scoped: dict[int, list[TypeInfo]] = defaultdict(list)
    for mention in mentions:
        match mention.kind:
            case MentionKind.TYPE:
                owner_name = mention.name
            case MentionKind.QUALIFIED_NAME:
                # `Type.method` の `Type` も、その節が型を名指したことになる
                owner_name = mention.name.rsplit(".", 1)[0]
            case (
                MentionKind.PATH
                | MentionKind.SUBCOMMAND
                | MentionKind.OPTION
                | MentionKind.STORED_FILE
            ):
                continue
            case _ as unreachable:
                assert_never(unreachable)
        for info in _class_like_types(world, owner_name):
            if info not in scoped[section_key(mention)]:
                scoped[section_key(mention)].append(info)
    return scoped


def _document_types(types_by_section: dict[int, list[TypeInfo]]) -> list[TypeInfo]:
    """設計書の全体が名指した型。全節の型の名指しの和集合。"""
    merged: list[TypeInfo] = []
    for infos in types_by_section.values():
        for info in infos:
            if info not in merged:
                merged.append(info)
    return merged


def _bare_members(
    world: _World, name: str, scope_types: list[TypeInfo], document_types: list[TypeInfo]
) -> tuple[list[Member], list[Member]]:
    """単独名の引き当て先のメンバーを、(確定, 曖昧) に分ける。

    絞りは三段で、先の段で当たればそこで止める。
    1. 同じ節が名指した型が持つメソッド。節に型の名指しがあるのに、その型のどれもが
       持たない名前は絞れていない名前なので、次の段へ流さない。
    2. 節に型の名指しが無いときは、設計書の全体が名指した型が持つメソッド。
    3. 全体の型でも当たらないときだけ、モジュール直下の関数。
    """
    if scope_types:
        found = [m for info in scope_types for m in info.members.get(name, [])]
    else:
        found = [m for info in document_types for m in info.members.get(name, [])]
        if not found:
            found = [
                m
                for index in world.indexes.indexes
                for info in index.types.values()
                if info.kind == "module"
                for m in info.members.get(name, [])
            ]
    found.sort(key=lambda m: (m.path, m.line, m.qualified_name))
    if len({m.owner for m in found}) > 1:
        return [], found
    return found, []


_LocationKey = tuple[str, int, str, LocationKind]


def _declarations(
    world: _World,
    mention: Mention,
    scope_types: list[TypeInfo],
    document_types: list[TypeInfo],
) -> tuple[list[Location], list[_Declaration], list[Location]]:
    name = mention.name
    bare = mention.kind is MentionKind.TYPE
    locations: dict[_LocationKey, None] = {}
    ambiguous: dict[_LocationKey, None] = {}
    declarations: list[_Declaration] = []
    for index in world.indexes.indexes:
        types = sorted(index.types.values(), key=lambda t: (t.path, t.line))
        _match_types(types, name, locations, declarations, bare=bare)
        if bare:
            continue
        _match_members(types, index.types, name, locations, declarations)
    if bare:
        _match_bare_members(
            world,
            name,
            scope_types,
            document_types,
            locations,
            ambiguous=ambiguous,
            declarations=declarations,
        )
    return (
        [Location(*key, world.is_untracked(key[0])) for key in locations],
        declarations,
        [Location(*key, world.is_untracked(key[0])) for key in ambiguous],
    )


def _match_types(
    types: list[TypeInfo],
    name: str,
    locations: dict[_LocationKey, None],
    declarations: list[_Declaration],
    *,
    bare: bool,
) -> None:
    """名前に当たる型を、引き当て先とたどりの起点に足す。"""
    for info in types:
        if not _suffix_match(info.qualified_name, name):
            continue
        kind = LocationKind(info.kind)
        if kind is LocationKind.MODULE:
            # ファイル全体を表す型。修飾名での名指しはファイルを候補にするが、
            # ファイル内の全関数をたどると候補が溢れるので、たどりの起点にはしない
            if not bare:
                locations[(info.path, info.line or 1, info.qualified_name, kind)] = None
            continue
        locations[(info.path, info.line, info.qualified_name, kind)] = None
        declarations.append(_Declaration(info, None))


def _match_members(
    types: list[TypeInfo],
    owners: Mapping[TypeKey, TypeInfo],
    name: str,
    locations: dict[_LocationKey, None],
    declarations: list[_Declaration],
) -> None:
    """名前に当たるメンバーを、引き当て先とたどりの起点に足す。"""
    members = sorted(
        (m for info in types for ms in info.members.values() for m in ms),
        key=lambda m: (m.path, m.line, m.qualified_name),
    )
    for member in members:
        if _suffix_match(member.qualified_name, name):
            locations[_member_key(owners.get(member.owner), member)] = None
            declarations.append(_Declaration(None, member))


def _match_bare_members(
    world: _World,
    name: str,
    scope_types: list[TypeInfo],
    document_types: list[TypeInfo],
    locations: dict[_LocationKey, None],
    *,
    ambiguous: dict[_LocationKey, None],
    declarations: list[_Declaration],
) -> None:
    """単独名の引き当て先のメンバーを、確定は引き当て先へ、曖昧は曖昧の側へ足す。"""
    owners = {key: info for index in world.indexes.indexes for key, info in index.types.items()}
    resolved, unresolved = _bare_members(world, name, scope_types, document_types)
    for member in resolved:
        locations[_member_key(owners.get(member.owner), member)] = None
        declarations.append(_Declaration(None, member))
    for member in unresolved:
        ambiguous[_member_key(owners.get(member.owner), member)] = None


def _member_key(owner: TypeInfo | None, member: Member) -> _LocationKey:
    at_module_level = owner is not None and owner.kind == "module"
    member_kind = (
        LocationKind.FUNCTION
        if at_module_level and member.kind == "method"
        else LocationKind(member.kind)
    )
    return (member.path, member.line, member.qualified_name, member_kind)


def _existing_paths(world: _World, name: str) -> list[Location]:
    normalized = PurePosixPath(name.removeprefix("./")).as_posix()
    if normalized.startswith(("..", "/")):
        # 作業ディレクトリの外は、引き当ての対象にしない
        return []
    if "/" not in normalized:
        return [
            Location(p, None, "ファイル", LocationKind.FILE, world.is_untracked(p))
            for p in world.paths
            if PurePosixPath(p).name == normalized
        ]
    if normalized in world.paths:
        return [
            Location(
                normalized, None, "ファイル", LocationKind.FILE, world.is_untracked(normalized)
            )
        ]
    # 設計書は、パッケージの根からの相対で書くことがある。末尾が一致するファイルを拾う
    tails = [p for p in world.paths if p.endswith("/" + normalized)]
    if tails:
        return [
            Location(p, None, "ファイル", LocationKind.FILE, world.is_untracked(p)) for p in tails
        ]
    prefix = normalized.rstrip("/") + "/"
    if any(p.startswith(prefix) for p in world.paths):
        # ディレクトリは引き当たったと数えるが、中のファイルを全て候補にはしない
        return [Location(prefix, None, "ディレクトリ", LocationKind.DIRECTORY)]
    return []


def _cli_paths(world: _World) -> list[str]:
    return [
        p for p in world.source_paths if "cli" in PurePosixPath(p).parts[:-1] and p.endswith(".py")
    ]


def _commands(world: _World) -> dict[str, list[Location]]:
    """CLI の副コマンドの名前から、その登録（add_parser）の位置。"""
    found: dict[str, list[Location]] = defaultdict(list)
    for path in _cli_paths(world):
        for number, text in enumerate(world.text(path).split("\n"), start=1):
            for match in _ADD_PARSER.finditer(text):
                found[match[1]].append(
                    Location(
                        path,
                        number,
                        f"副コマンド {match[1]}",
                        LocationKind.SUBCOMMAND,
                        world.is_untracked(path),
                    )
                )
    return found


def _command_files(
    mentions: tuple[Mention, ...], commands: dict[str, list[Location]]
) -> dict[int, list[str]]:
    """節ごとに、その節が名指した副コマンドの実装のファイル。引数の引き当てを絞る。"""
    scoped: dict[int, list[str]] = defaultdict(list)
    for mention in mentions:
        if mention.kind is not MentionKind.SUBCOMMAND:
            continue
        key = section_key(mention)
        for location in commands.get(mention.name, []):
            if location.path not in scoped[key]:
                scoped[key].append(location.path)
    return scoped


def _quoted_pattern(name: str, *, prefixed: bool) -> re.Pattern[str]:
    head = r"""(?:[^"'\s]*/)?""" if prefixed else ""
    return re.compile(rf"""["']{head}{re.escape(name)}["']""")


def _quoted_in(
    world: _World, name: str, paths: Iterable[str], kind: LocationKind, *, prefixed: bool = False
) -> list[Location]:
    """名前を引用符で囲んだ文字列として持つファイルと最初の行。prefixed なら `dir/name` も拾う。"""
    pattern = _quoted_pattern(name, prefixed=prefixed)
    found: list[Location] = []
    for path in sorted(set(paths)):
        for number, text in enumerate(world.text(path).split("\n"), start=1):
            if pattern.search(text):
                found.append(Location(path, number, f"`{name}` を持つ", kind))
                break
    return found


def _constant_names(world: _World, name: str, paths: Iterable[str]) -> dict[str, set[str]]:
    """保存物の名前を持つ全ての行から、`NAME = "..."` の形の定数の宣言を探す。パスごとの定数名。"""
    pattern = _quoted_pattern(name, prefixed=True)
    found: dict[str, set[str]] = defaultdict(set)
    for path in sorted(set(paths)):
        if not path.endswith(".py"):
            # 参照の判定が Python の import に依るので、他の言語の定数は追わない
            continue
        for text in world.text(path).split("\n"):
            declared = _CONSTANT_DECLARATION.match(text)
            if declared and pattern.search(text):
                found[path].add(declared[1])
    return found


def _stored_constant_values(world: _World) -> frozenset[str]:
    """ソースが `NAME = "x.json"` の形の定数として持つ保存物の名前（ディレクトリを除く）。"""
    values: set[str] = set()
    for path in world.source_paths:
        if not path.endswith(".py"):
            # 定数の宣言を読めるのは Python だけ（_constant_names と同じ）
            continue
        for text in world.text(path).split("\n"):
            declared = _CONSTANT_VALUE.match(text)
            if declared:
                name = declared[1].rsplit("/", 1)[-1]
                if name.rsplit(".", 1)[-1] in STORED_FILE_SUFFIXES:
                    values.add(name)
    return frozenset(values)


def _module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _same_module(imported: str, defining: str) -> bool:
    # src/ のような、import の名前に現れない上位のディレクトリを許す
    return bool(imported) and (defining == imported or defining.endswith("." + imported))


def _import_forms(
    tree: ast.AST, importer: str, defining: str, name: str
) -> tuple[set[str], set[str]]:
    """importer が定数を束ねる名前（直接の import）と、定義したモジュールを束ねる名前の集合。"""
    package = _module_name(importer).split(".")
    if not importer.endswith("/__init__.py"):
        package = package[:-1]
    direct_names: set[str] = set()
    module_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = package[: len(package) - (node.level - 1)] if node.level else []
            source = ".".join([*base, *([node.module] if node.module else [])])
            for alias in node.names:
                if _same_module(source, defining) and alias.name == name:
                    direct_names.add(alias.asname or alias.name)
                elif _same_module(f"{source}.{alias.name}".strip("."), defining):
                    module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _same_module(alias.name, defining):
                    module_names.add(alias.asname or alias.name)
    return direct_names, module_names


def _references(
    function: ast.AST, name: str, direct_names: set[str], module_names: set[str]
) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and node.id in direct_names:
            return True
        if (
            isinstance(node, ast.Attribute)
            and node.attr == name
            and ast.unparse(node.value) in module_names
        ):
            return True
    return False


@dataclass(frozen=True)
class _Accessor:
    """保存物の定数を参照する関数。line は呼び出しの辺（_Edges）の鍵に使う宣言の行。"""

    path: str
    function: str
    line: int
    # 関数の中に書き込みの呼び出しがある
    writes: bool


# 書き込みの呼び出しと数える名前。`write_text`、`write_bytes`、jigane の `write_text_atomic` などは
# `write` で始まる名前と `atomic` を含む名前で拾い、ここには残りを置く
_WRITE_PREFIX = "write"
_WRITE_INFIX = "atomic"
# 修飾の有無を問わない（`json.dump`、`yaml.dump`、`pickle.dump`、修飾なしの `dump`）
_WRITE_CALLS = frozenset({"dump", "rename"})
# 引数が二つの置き換え。`str.replace(old, new)` と形が同じなので、モジュール名つきで見分ける
_REPLACE_CALLS = frozenset({"os.replace", "shutil.move"})
# 書き込みの mode。更新（`+`）も書き込みに数える
_WRITE_MODES = frozenset("wax+")


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _opens_for_writing(call: ast.Call) -> bool:
    """`open(path, "w")`、`path.open("a")`、`open(path, mode="x")` の形。"""
    name = _call_name(call)
    if name != "open":
        return False
    candidates = [k.value for k in call.keywords if k.arg == "mode"]
    # 組み込みの open は mode が二番目、Path.open は一番目の位置引数
    positions = (1,) if isinstance(call.func, ast.Name) else (0,)
    candidates += [call.args[i] for i in positions if len(call.args) > i]
    return any(
        isinstance(c, ast.Constant) and isinstance(c.value, str) and _WRITE_MODES & set(c.value)
        for c in candidates
    )


def _writes(function: ast.AST) -> bool:
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node).lstrip("_")
        if name.startswith(_WRITE_PREFIX) or _WRITE_INFIX in name or name in _WRITE_CALLS:
            return True
        if _opens_for_writing(node):
            return True
        if ast.unparse(node.func) in _REPLACE_CALLS:
            return True
        # `Path.replace(target)` は引数が一つ。`str.replace(old, new)` は二つで、書き込みではない
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "replace"
            and len(node.args) == 1
            and not node.keywords
        ):
            return True
    return False


def _stored_accessors(world: _World, name: str, locations: list[Location]) -> list[_Accessor]:
    """保存物の名前を持つ定数を、関数の中で参照する関数。一段のたどり。

    参照は、定数を定義したモジュールからの import（`from mod import NAME`）か、
    モジュール経由の参照（`mod.NAME`）に限る。同じ名前の無関係な識別子は拾わない。
    定義したファイルは、すでに直接の候補なので含めない。関数の外（import や別の定数の宣言）の
    参照は、読み書きの現場ではないので数えない。言語は Python だけ。
    """
    constants = _constant_names(world, name, (loc.path for loc in locations))
    found: dict[_Accessor, None] = {}
    for path in world.source_paths:
        if path in constants or not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(world.text(path))
        except (SyntaxError, RecursionError, MemoryError, ValueError):
            # 構文の解けないファイルは、参照の有無を言えない。
            # 入れ子の深い式は RecursionError、巨大な入力は MemoryError、
            # NUL 文字は ValueError で落ちる
            continue
        functions = [
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for defining, names in constants.items():
            module = _module_name(defining)
            for constant in sorted(names):
                direct_names, modules = _import_forms(tree, path, module, constant)
                for function in functions:
                    if _references(function, constant, direct_names, modules):
                        # 呼び出しの辺の鍵は、デコレータの有無で宣言の行が変わりうるので両方を引く
                        lines = {function.lineno, *(d.lineno for d in function.decorator_list)}
                        for line in sorted(lines):
                            found[_Accessor(path, function.name, line, _writes(function))] = None
    return list(found)


def _trace(
    world: _World,
    declaration: _Declaration,
    mention: Mention,
    tallies: dict[str, _Tally],
) -> None:
    """引き当てた宣言から、呼び先と呼び手のファイルを一段だけ足す。"""
    edges = world.edges
    callees: set[str] = set()
    callers: set[str] = set()
    own_paths: set[str] = set()
    if declaration.member is not None:
        member = declaration.member
        own_paths.add(member.path)
        callers |= edges.callers_of_member.get((member.path, member.line), set())
        callees |= edges.callees.get((member.path, member.line), set())
    elif declaration.type_info is not None:
        info = declaration.type_info
        own_paths.update(info.paths or (info.path,))
        callers |= edges.callers_of_type.get(info.key, set())
        for members in info.members.values():
            for member in members:
                callees |= edges.callees.get((member.path, member.line), set())
    for path in sorted(callers - own_paths):
        tallies[path].add(CallerReason(mention))
    for path in sorted(callees - own_paths):
        tallies[path].add(CalleeReason(mention))
