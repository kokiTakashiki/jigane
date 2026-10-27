"""方針のレビューに渡す、判別の流れ図と、任意の項目の読む側の対応。

どちらも関数の本体から外形の事実だけを取る。判別の流れ図は、列挙型の値を返す関数の分岐の条件の原文と
返す値だけ。読む側の対応は、任意の項目への属性の参照を持つ公開の関数の名前だけ。
"""

from collections.abc import Iterable, Sequence
from pathlib import Path

from jigane.figures.models import MermaidFigure, Panel
from jigane.git.tree_files import walk_tree_files
from jigane.judgment_map.classification import Segment
from jigane.languages.base import Branch, Return, Span, Step
from jigane.languages.base import Member as MemberRef
from jigane.policy_materials import er
from jigane.policy_materials.classes import _changed_types, _flat
from jigane.preconditions.required import required
from jigane.reviews.lifecycle import Review
from jigane.sequence.index import Index, Member, TypeInfo
from jigane.sequence.indexes import Indexes
from jigane.sequence.resolve import receiver_typer

_QUALIFIED_MIN_PARTS = 2  # 列挙型と値

SECTION_DISCRIMINATORS = "判別の流れ図"
SECTION_READERS = "読む側の対応"
DISCRIMINATOR_LEGEND = (
    "変更した範囲の関数のうち、列挙型の値を返す分岐を持つものの、条件の原文と、その腕で返す列挙型の値だけ。"
    "腕の中のそれ以外の文は描かない。菱形が条件、角の丸い箱が返す値。"
    "Python と Java の関数だけを読む。"
)
READER_LEGEND = (
    "ER 図の任意の項目ごとに、その型（または派生の型）の項目を属性として参照する（`.項目名`）"
    "公開のメソッドと関数。受け手の型は、シーケンス図と同じ規則で、型注記と生成の初期化子から解いた。"
    "項目の脇の「読む側」が、型を確かめられた関数の数。"
    "「受け手の型を確かめられない参照」は、項目名だけが一致し、型注記が無いなどで型が決まらなかった参照の数で、"
    "読む側には含めない。"
)
_ATTRIBUTE_NODES = {"attribute", "member_access_expression", "navigation_expression"}

Pending = list[tuple[str, str]]


def _label(text: str) -> str:
    return _flat(text).replace('"', "'")


class _Flow:
    """言語アダプタが返した分岐から、条件と返す列挙型の値だけの有向グラフを作る。"""

    def __init__(self, enums: set[str]) -> None:
        self.enums = enums
        self.nodes: dict[str, tuple[str, str]] = {}
        self.edges: list[tuple[str, str, str]] = []

    def new(self, kind: str, text: str) -> str:
        name = f"n{len(self.nodes)}"
        self.nodes[name] = (kind, text)
        return name

    def link(self, pending: Pending, target: str) -> None:
        self.edges.extend((origin, target, label) for origin, label in pending)

    def enum_value(self, step: Return) -> str | None:
        if step.value is None:
            return None
        parts = step.value.split(".")
        return (
            step.value if len(parts) >= _QUALIFIED_MIN_PARTS and parts[-2] in self.enums else None
        )

    def walk(self, steps: Iterable[Step], pending: Pending) -> Pending:
        for step in steps:
            if isinstance(step, Return):
                value = self.enum_value(step)
                if value is not None:
                    self.link(pending, self.new("leaf", f"return {value}"))
                # 他の値を返す文も、流れはここで終わる
                return []
            pending = self.walk_branch(step, pending)
        return pending

    def walk_branch(self, branch: Branch, pending: Pending) -> Pending:
        decision = self.new("decision", branch.condition)
        self.link(pending, decision)
        exits: Pending = []
        for arm in branch.arms:
            exits += self.walk(arm.steps, [(decision, arm.label)])
        if branch.fallthrough_label is not None:
            exits.append((decision, branch.fallthrough_label))
        return exits

    def pruned(self) -> tuple[dict[str, tuple[str, str]], list[tuple[str, str, str]]]:
        """返す値に至らない条件を落とす。"""
        alive = {n for n, (kind, _) in self.nodes.items() if kind == "leaf"}
        grown = True
        while grown:
            grown = False
            for origin, target, _ in self.edges:
                if target in alive and origin not in alive:
                    alive.add(origin)
                    grown = True
        nodes = {n: v for n, v in self.nodes.items() if n in alive}
        edges = [e for e in self.edges if e[0] in alive and e[1] in alive]
        return nodes, edges


def _changed_callables(
    indexes: Indexes, segments: Sequence[Segment]
) -> list[tuple[Index, Member]]:
    spans = [
        (s.new_path, s.new_span, s.qualified_name)
        for s in segments
        if s.new_path is not None and s.new_span is not None
    ]
    found = [
        (index, member)
        for index in indexes.indexes
        for member in index.callables.values()
        if any(_touches(member, path, span, name) for path, span, name in spans)
    ]
    return sorted(found, key=lambda pair: (pair[1].path, pair[1].line))


def _touches(member: Member, path: str, span: Span, name: str) -> bool:
    """変更後の側の segment が、関数の本体の行に届くか。

    宣言に帰属した segment の範囲は、その宣言の飛び飛びの変更行を覆う。範囲が関数に重なっても、
    名前が別の宣言（外側の型）の segment は、その関数の行を変えていない。名前が空の segment は
    宣言の外の連続した変更で、範囲がそのまま変更行になる。
    """
    declaration = member.declaration
    # Member.qualified_name はモジュール名が前に付き、segment の名前は宣言の修飾名
    own_name = declaration.qualified_name if declaration is not None else member.qualified_name
    if member.path != path or name not in ("", own_name):
        return False
    if declaration is not None:
        first, last = declaration.span.start_line, declaration.span.end_line
    else:
        first, last = member.node.start_point[0] + 1, member.node.end_point[0] + 1
    return first <= span.end_line and span.start_line <= last


def _render(
    member: Member, nodes: dict[str, tuple[str, str]], edges: list[tuple[str, str, str]]
) -> Panel:
    # 図の先頭の `%%` の行は、関数の名前と位置。見出しにも同じ文を使う
    heading = f"{member.qualified_name} {member.path}:{member.line}"
    lines = [f"%% {heading}", "flowchart TD"]
    for name, (kind, text) in nodes.items():
        if kind == "decision":
            lines.append(f'    {name}{{"{_label(text)}"}}')
        elif kind == "leaf":
            lines.append(f'    {name}("{_label(text)}")')
        else:
            lines.append(f'    {name}["{_label(text)}"]')
    for origin, target, label in edges:
        arrow = f"-->|{_label(label)}|" if label else "-->"
        lines.append(f"    {origin} {arrow} {target}")
    return Panel(heading, "\n".join(lines))


def _indexes_of(review: Review) -> Indexes:
    root = Path(review.repository_root)
    paths = walk_tree_files(root, root) if review.is_diff else review.paths
    return Indexes(root, paths)


def discriminator_diagram(review: Review) -> MermaidFigure | None:
    """判別の流れ図。列挙型の値を返す関数が無ければ None。"""
    indexes = _indexes_of(review)
    enums = {t.name for t in indexes.types() if er.is_enum_like(t)}
    if not enums:
        return None
    callables = _changed_callables(indexes, review.map_kind.segments)
    skipped = 0
    blocks: list[Panel] = []
    for index, member in callables:
        body = index.adapter.body_of(member.node)
        if body is None:
            continue
        steps = index.adapter.discriminator_branches(
            body, required(index.trees[member.path].root_node.text)
        )
        if steps is None:
            skipped += 1
            continue
        flow = _Flow(enums)
        start = flow.new("start", member.qualified_name)
        flow.walk(steps, [(start, "")])
        nodes, edges = flow.pruned()
        if not any(kind == "leaf" for kind, _ in nodes.values()):
            continue
        blocks.append(_render(member, nodes, edges))
    if not blocks:
        return None
    note = (
        f"対応言語の外で省いたもの: Python と Java 以外の言語の関数 {skipped} 件。"
        if skipped
        else None
    )
    return MermaidFigure(DISCRIMINATOR_LEGEND, tuple(blocks), note)


def _attribute_reads(
    index: Index, member: Member, field_name: str, owner: TypeInfo
) -> tuple[int, int]:
    """(owner の項目を読む参照の数, 受け手の型を確かめられない参照の数)。"""
    body = index.adapter.body_of(member.node)
    if body is None:
        return 0, 0
    typer = receiver_typer(index, member)
    confirmed = unverified = 0
    stack = [body]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type not in _ATTRIBUTE_NODES:
            continue
        kind = index.adapter.receiver_of(node)
        if (
            not isinstance(kind, MemberRef)
            or index.adapter.simple_type_name(kind.name) != field_name
        ):
            continue
        typed = typer(kind.base)
        if not typed:
            # 型注記の無い仮引数は、解決の側では空の名前の外部の型になる。型が無いものに寄せる
            unverified += 1
        elif isinstance(typed, TypeInfo) and any(
            t.qualified_name == owner.qualified_name for t in index.hierarchy(typed)
        ):
            confirmed += 1
        # 型が決まり、ER 図の型でも派生でもないもの（索引に無い型を含む）は別の型の項目で、数えない
    return confirmed, unverified


def _is_public(member: Member) -> bool:
    if member.declaration is not None:
        return member.declaration.is_public
    return not member.name.startswith("_")


def reader_diagram(review: Review) -> MermaidFigure | None:
    """読む側の対応。ER 図に任意の項目が無ければ None。"""
    indexes = _indexes_of(review)
    changed = _changed_types(indexes, review.map_kind.segments)
    ordered = er.er_types(indexes, changed)
    optional = [(info, m) for info in ordered for m in er.data_fields(info) if er.is_optional(m)]
    if not optional:
        return None
    lines = ["flowchart LR"]
    for number, (info, field) in enumerate(optional):
        found: list[Member] = []
        unverified = 0
        for index in indexes.indexes:
            for member in index.callables.values():
                if not _is_public(member):
                    continue
                confirmed, unknown = _attribute_reads(index, member, field.name, info)
                unverified += unknown
                if confirmed:
                    found.append(member)
        readers = sorted(found, key=lambda m: (m.path, m.line))
        node = f"f{number}"
        display = indexes.display(info)
        suffix = f"、受け手の型を確かめられない参照 {unverified} 件" if unverified else ""
        lines.append(
            f'    {node}["{_label(display)}.{_label(field.name)} (読む側 {len(readers)}{suffix})"]'
        )
        for position, reader in enumerate(readers):
            lines.append(
                f'    {node} --> {node}r{position}["{_label(reader.qualified_name)} '
                f'{reader.path}:{reader.line}"]'
            )
    return MermaidFigure(READER_LEGEND, (Panel("", "\n".join(lines)),))
