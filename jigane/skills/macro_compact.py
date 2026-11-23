"""Codex の方針レビューに渡す、役ごとの静的資料。"""

import re
from collections import Counter
from dataclasses import replace

from jigane import groups
from jigane.figures.models import (
    MovedSignature,
    Panel,
    SequenceFigure,
    SignatureChange,
    SignatureEntry,
    TableFigure,
)
from jigane.groups.viewpoints import KISS, SEPARATION_OF_CONCERNS, SINGLE_SOURCE, YAGNI, Viewpoint
from jigane.judgment_map.changes import (
    CHANGES_FILE,
    OMITTED_BINARY,
    OMITTED_GENERATED,
    OMITTED_LIMIT,
    OMITTED_TOO_LARGE,
    split_file_sections,
)
from jigane.languages import registry
from jigane.llm_runs.prompt_stamp import PromptStamp
from jigane.markdown.fence import fence, one_line
from jigane.policy_materials import changed_files, compact, discriminators, signature_evidence
from jigane.policy_materials.compact import ChangeNodes
from jigane.policy_materials.type_deltas import TypeDeltas, render_member
from jigane.preconditions.required import required
from jigane.repository_docs.kinds import Launch
from jigane.repository_docs.section import document_section
from jigane.reviews.catalog import scope_files
from jigane.reviews.lifecycle import Review
from jigane.reviews.stored_pairs import PairsNotSavedError, load_pairs
from jigane.skills import (
    adversarial,
    macro,
    policy_callers,
    policy_figures,
    policy_principle_shapes,
    policy_principles,
    policy_stores,
    policy_value_sets,
    shared_rules,
)
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_launch import ExtraLaunch
from jigane.skills.policy_principle_shapes import shared_type_deltas
from jigane.storage.review_access import read_stored_file_if_present

PROMPT = "macro_codex"
P1_GUIDE = "macro_codex_p1_guide"
P3_GUIDE = "macro_codex_p3_guide"
_DIAGRAM_ROLE = (
    "変更の目的と、公開・保存契約、依存の向き、責務、技術選定を点検する。"
    "使う側に大きな手戻りを生む問題を残し、直す最初の一手が範囲内なら in_scope を true とする。"
    "範囲外なら scope_reason に理由を書く。重複は統合し、merged と references は空とする。"
    "根拠不足や矛盾で見送る候補は discarded に理由と懸念を記録する。"
)
_DIAGRAM_KEYS = (
    "commands",
    "stores",
    "discriminators",
)
_MARKS_KEY = changed_files.PrebuiltSection.MARKS


def _change_nodes(review: Review) -> ChangeNodes:
    scope = scope_files(review)
    if not review.is_diff:
        return compact.change_nodes(review, scope, {})
    try:
        pairs = load_pairs(review.id)
    except PairsNotSavedError:
        nodes = compact.change_nodes(review, scope, {})
        return replace(nodes, text=nodes.text + "\n旧版記録のため省略理由を照合できない。")
    if not pairs:
        return compact.change_nodes(review, scope, {})
    diff = read_stored_file_if_present(review.id.directory(), CHANGES_FILE)
    if diff is None:
        nodes = compact.change_nodes(review, scope, {})
        return replace(nodes, text=nodes.text + "\n差分記録がなく、省略理由を照合できない。")
    markers = {OMITTED_BINARY, OMITTED_GENERATED, OMITTED_LIMIT, OMITTED_TOO_LARGE}
    omissions = {}
    for pair, section in zip(pairs, split_file_sections(diff, pairs), strict=True):
        path = pair.new_path or pair.old_path
        body = section[2:]
        if path and body and body[0] in markers:
            omissions[path] = body[0]
    return compact.change_nodes(review, scope, omissions)


_CALL = re.compile(r"^\s*(P\d+)(?:->>|--x)(P\d+): ")
_PARTICIPANT = re.compile(r"^\s*participant (P\d+) as ")
_CHANGE = re.compile(r"\[J\d+\]|（追加）|（削除）|（呼び先の変更:")


def _changes_in_panel(panel: Panel) -> tuple[str, int]:
    lines = panel.mermaid.splitlines()
    endpoints: set[str] = set()
    edges = []
    omitted = 0
    for line in lines:
        edge = _CALL.match(line)
        if edge is None:
            continue
        if _CHANGE.search(line):
            endpoints.update(edge.groups())
            edges.append(line.strip().replace(": ", " ", 1))
        else:
            omitted += 1
    nodes = [
        f"{match[1]}={line[match.end() :]}"
        for line in lines
        if (match := _PARTICIPANT.match(line)) and match[1] in endpoints
    ]
    notes = [line.split(": ", 1)[1] for line in lines if line.lstrip().startswith("Note over ")]
    counted = [f"{edge} ×{count}" if count > 1 else edge for edge, count in Counter(edges).items()]
    return "\n".join([*nodes, *counted, *notes]), omitted


def _entry_title(title: str, nodes: ChangeNodes) -> str:
    name, sep, place = title.rpartition("（")
    return one_line(f"{name}{sep}{nodes.place(place)}" if sep else title)


def _sequence_text(sequence: SequenceFigure, nodes: ChangeNodes) -> str:
    lines = [
        "### 呼び出しの流れ",
        "札・増減印のある矢印と両端。入口・前後ごとに同じ矢印を集約。×N は出現数。",
        "括弧内は札・増減印のない省略矢印数。全体の注記は末尾。",
    ]
    for flow in sequence.without_tagged_before().flows:
        lines.append(_entry_title(flow.title, nodes))
        for panel in flow.panels:
            text, omitted = _changes_in_panel(panel)
            lines.append(f"{one_line(panel.title)} ({omitted})")
            if text:
                lines.append(fence(text))
        if flow.note is not None:
            lines.append(flow.note.material)
    lines.extend(one_line(note.material) for note in sequence.notes)
    return "\n".join(lines)


def _boundary_calls(sequence: SequenceFigure, nodes: ChangeNodes) -> str:
    """各入口の変更に関わる別の参加者への矢印を、一つずつ示す。"""
    lines = [
        "### 入口ごとの代表的な依存",
        (
            "札・増減印のある別の参加者への矢印を各入口で一件、変更後を優先して掲載。"
            "同じ参加者内の呼び出しと残りの矢印は原則 P4 の資料を参照。"
        ),
    ]
    samples = []
    missing = 0
    for flow in sequence.without_tagged_before().flows:
        sample = next(
            (sample for panel in reversed(flow.panels) if (sample := _boundary_call(panel))),
            "",
        )
        if sample:
            samples.append(f"{_entry_title(flow.title, nodes)} {sample}")
        else:
            missing += 1
    if samples:
        lines.append(fence("\n".join(samples)))
    if missing:
        lines.append(f"条件を満たす矢印のない入口 {missing} 件は省略。")
    lines.extend(one_line(note.material) for note in sequence.notes)
    return "\n".join(lines)


def _boundary_call(panel: Panel) -> str:
    participants: dict[str, str] = {}
    for line in panel.mermaid.splitlines():
        if match := _PARTICIPANT.match(line):
            participants[match[1]] = line[match.end() :]
        edge = _CALL.match(line)
        if edge and edge[1] != edge[2] and _CHANGE.search(line):
            source = participants.get(edge[1], edge[1])
            target = participants.get(edge[2], edge[2])
            return f"{panel.title}: {source} → {target}: {line[edge.end() :]}"
    return ""


def _signature_text(review: Review, nodes: ChangeNodes, *, summarize: bool) -> str:
    lines = ["### 公開のシグネチャの変更前後"]
    if nodes.packed:
        lines.append("+ は新規、- は削除。~ の行の ⟦旧¦新⟧ は片側を選び、共通部分につなぐ。")
    adapters = registry.adapters()
    omitted = 0
    counts: Counter[SignatureChange] = Counter()
    for entry in changed_files.signature_changes(review).entries:
        if registry.is_test_path(entry.place.rpartition(":")[0], adapters):
            omitted += 1
            continue
        counts[entry.change] += 1
        if summarize and counts[entry.change] > 1:
            continue
        lines.append(_signature_line(entry, nodes))
    if summarize and any(count > 1 for count in counts.values()):
        omitted_counts = "、".join(
            f"{change.value} {count - 1} 件" for change, count in counts.items() if count > 1
        )
        lines.append(
            f"変更種別ごとに代表 1 件。省略は {omitted_counts}。署名と位置の全体は原則 P3。"
        )
    if omitted:
        lines.append(f"テストのシグネチャ {omitted} 件は省略。位置は判断箇所を参照。")
    return "\n".join(lines)


def _kiss_signatures(review: Review, nodes: ChangeNodes) -> str:
    adapters = registry.adapters()
    entries = changed_files.signature_changes(review).entries
    included = [
        entry
        for entry in entries
        if not registry.is_test_path(entry.place.rpartition(":")[0], adapters)
    ]
    parts = [signature_evidence.comparison_text(included, nodes.place)]
    if omitted := len(entries) - len(included):
        parts.append(f"テストのシグネチャ {omitted} 件は省略。位置は判断箇所を参照。")
    if any(parts):
        return "\n".join([f"### {changed_files.SECTION_SIGNATURES}", *filter(None, parts)])
    return ""


def _signatures(assembly: AssemblyInput, nodes: ChangeNodes, *, is_diagram: bool) -> str:
    if not is_diagram:
        return _kiss_signatures(assembly.review, nodes)
    summarize = KISS in policy_principles.applicable_principles(assembly)
    return _signature_text(assembly.review, nodes, summarize=summarize)


def _signature_line(entry: SignatureEntry, nodes: ChangeNodes) -> str:
    place = nodes.place(entry.place)
    if isinstance(entry, MovedSignature):
        return f"{nodes.place(entry.old_place)} → {place} {entry.text}"
    old, new = entry.old_text(), entry.new_text()
    if nodes.packed:
        if entry.change is SignatureChange.ADDED:
            return f"{place} + {new}"
        if entry.change is SignatureChange.DELETED:
            return f"{place} - {old}"
        if not any(mark in old or mark in new for mark in "⟦¦⟧"):
            shared = _signature_change(old, new)
            if len(shared.encode("utf-8")) < len(f"{old} → {new}".encode()):
                return f"{place} {shared}"
    return f"{place} {old} → {new}"


def _signature_change(old: str, new: str) -> str:
    prefix = compact.shared_prefix(old, new)
    old, new = old[len(prefix) :], new[len(prefix) :]
    suffix = compact.shared_prefix(old[::-1], new[::-1])[::-1]
    return f"~ {prefix}⟦{old[: len(old) - len(suffix)]}¦{new[: len(new) - len(suffix)]}⟧{suffix}"


def _reader_text(review: Review, nodes: ChangeNodes) -> str:
    heading = [
        "### 読む側の対応",
        "型が確定した公開の読み手数と型不明の参照数。各項目から本番優先で代表 1 件を掲載。",
        "他の読み手の位置は省略。代表の位置は参照式でなく宣言。",
    ]
    lines = []
    adapters = registry.adapters()
    for owner, field, readers, unknown in discriminators.reader_rows(review):
        reader = min(readers, key=lambda m: registry.is_test_path(m.path, adapters), default=None)
        location = f" {nodes.path(reader.path)}:{reader.line} {reader.name}" if reader else ""
        lines.append(f"{owner}.{field} {len(readers)}/{unknown}{location}")
    return "\n".join([*heading, *lines]) if lines else ""


def _private_type_text(result: TypeDeltas, nodes: ChangeNodes) -> str:
    members = policy_principle_shapes.private_type_members(result)
    if not members:
        return ""
    lines = [
        "### 公開の項目に現れる内部型の候補",
        "Python の公開項目に現れるアンダースコア付きの型名と宣言位置。同名の型は定義候補。",
    ]
    names: set[str] = set()
    for member, annotated in members:
        owner = result.indexes.find(member.owner)
        prefix = f"{result.indexes.display(owner)}." if owner and owner.kind != "module" else ""
        lines.append(f"{nodes.path(member.path)}:{member.line} {prefix}{render_member(member)}")
        names.update(annotated)
    for name in sorted(names):
        for info in result.indexes.named(name):
            adapter = registry.adapter_for(info.path)
            if adapter is not None and adapter.name == "python":
                lines.append(f"型 {nodes.path(info.path)}:{info.line} {info.qualified_name}")
    return "\n".join(lines)


def _command_text(assembly: AssemblyInput, nodes: ChangeNodes) -> str:
    figure = required(
        next((entry for entry in policy_figures.REGISTRY if entry.key == "commands"), None)
    )
    built = policy_figures.build_figure(figure, assembly)
    if not isinstance(built, TableFigure):
        return ""
    lines = [f"### {figure.title}", built.description]
    groups: dict[tuple[str, str], list[str]] = {}
    for row in built.rows:
        path, command, names, *details = row.cells
        fields = [
            f"{label}={value}"
            for label, value in zip(built.header[3:], details, strict=True)
            if value
        ]
        groups.setdefault((nodes.path(path), command), []).append(" ".join([names, *fields]))
    for (path, command), rows in groups.items():
        lines.extend((f"{path} {command}", *rows))
    return "\n".join(lines)


def _figure_texts(
    assembly: AssemblyInput,
    sections: policy_figures.PolicySections,
    keys: tuple[str, ...],
    nodes: ChangeNodes,
) -> list[str]:
    parts = policy_figures.prompt_figure_texts(
        assembly, sections.figures, set(keys) - {"commands", "readers", "stores"}
    )
    if "readers" in keys:
        parts.append(_reader_text(assembly.review, nodes))
    if "commands" in keys:
        parts.append(_command_text(assembly, nodes))
    if "stores" in keys:
        parts.append(
            assembly.remember(
                "compact_stores", lambda: policy_stores.compact_text(assembly, nodes)
            )
        )
    return parts


def stamp(text: str) -> PromptStamp:
    return macro.stamp_template(PROMPT, text)


def _principle_guide(viewpoint: str) -> str:
    guide_name = {SINGLE_SOURCE.number: P1_GUIDE, KISS.number: P3_GUIDE}.get(viewpoint)
    return adversarial.template_text(guide_name) if guide_name is not None else ""


def stamp_principle(launch: ExtraLaunch) -> PromptStamp:
    guide = _principle_guide(launch.viewpoint)
    return macro.stamp_template(PROMPT, launch.prompt, instruction=guide)


def _principle_role(principle: Viewpoint) -> str:
    role = (
        policy_principles.principle_text(principle)
        + "\nこの原則の番号を viewpoint に使う。違反は critical または warning、迷う候補は nit。"
    )
    guide = _principle_guide(principle.number)
    return f"{role}\n\n{guide}" if guide else role


def build_prompt(assembly: AssemblyInput, principle: Viewpoint | None = None) -> str:
    review = assembly.review
    sections = policy_figures.shared_policy_sections(assembly)
    is_diagram = principle is None
    information_hiding = principle == groups.viewpoints.INFORMATION_HIDING
    keys = (
        _DIAGRAM_KEYS
        if principle is None
        else tuple(
            key
            for key in policy_principles.figure_keys_of(principle)
            if key not in {"classes", "tree", "callers", "sequence"}
            and (key != "readers" or principle == SEPARATION_OF_CONCERNS or information_hiding)
        )
    )
    role = (
        _DIAGRAM_ROLE + "\n" + "過去指摘との重複を避ける。同じ論点は今回の根拠を示す。"
        "深掘りなら deepens に前回番号を書く。"
        + "\n観点番号:\n"
        + shared_rules.values_for(macro.DIAGRAM_PROMPT)["viewpoints"]
        if principle is None
        else _principle_role(principle)
    )
    nodes = assembly.remember("compact_nodes", lambda: _change_nodes(review))
    parts = [nodes.text]
    if information_hiding:
        role += (
            "\n書く側と読む側で鍵が一致していても、複数の箇所が内部の保存形式を"
            "直接扱うときは、形式変更がどこに波及するかを確かめる。"
        )
        deltas = shared_type_deltas(assembly)
        parts.append(_private_type_text(deltas, nodes))
        parts.append(policy_stores.compact_text(assembly, nodes, indexes=deltas.indexes))
        keys = tuple(key for key in keys if key != "stores")
    if is_diagram:
        parts.append(_boundary_calls(sections.sequence, nodes))
    if principle == SEPARATION_OF_CONCERNS:
        parts.append(
            assembly.remember("compact_sequence", lambda: _sequence_text(sections.sequence, nodes))
        )
    if is_diagram or principle == YAGNI:
        parts.append(
            assembly.remember(
                ("compact_callers", is_diagram),
                lambda: policy_callers.compact_callers(
                    review,
                    assembly.remember(
                        "compact_public_names", lambda: policy_callers.public_names(review)
                    ),
                    nodes,
                    cross_file_only=is_diagram,
                ),
            )
        )
    if is_diagram or principle == KISS:
        parts.append(
            assembly.remember(
                "compact_type_changes",
                lambda: compact.type_changes(shared_type_deltas(assembly), nodes),
            )
        )
        parts.append(_signatures(assembly, nodes, is_diagram=is_diagram))
    elif principle == YAGNI:
        parts.append(compact.implementation_edges(shared_type_deltas(assembly), nodes))
    if principle == SINGLE_SOURCE:
        parts.append(policy_value_sets.compact_p1_text(assembly))
        keys = tuple(key for key in keys if key != "value_sets")
    parts.extend(_figure_texts(assembly, sections, keys, nodes))
    parts.append(sections.figures.get(_MARKS_KEY, ""))
    if is_diagram:
        parts.extend(
            (
                sections.packed_technology if nodes.packed else sections.technology,
                macro.bot_findings_section(review) or "",
                ("呼び出しは公開 API の代表を参照。入口別の流れは原則 P4 が扱う。"),
            )
        )
    if principle == YAGNI:
        parts.append("未使用候補は公開 API の同名照合を参照。入口別の流れは原則 P4 が扱う。")
    materials = (
        compact.shared_fragments(parts) if nodes.packed else "\n\n".join(filter(None, parts))
    )
    tail = [
        f"### {macro.SECTION_HISTORY}\n\n{macro.history_text(assembly)}",
        document_section(
            assembly.docs, Launch.POLICY_DIAGRAM if is_diagram else Launch.POLICY_PRINCIPLE
        ),
        (
            "### 省略・解析上の制約\n\n"
            "クラス図、背景履歴、役に対応しない図表は省略。"
            "資料の原文や参照先が未収録なら内容を推測しない。"
        ),
        *(
            f"- {item.title} — {item.note()}"
            for item in policy_figures.unsupported_figures(review)
        ),
    ]
    return adversarial.load_prompt(PROMPT).substitute(
        role=role,
        mode=review.mode,
        declaration=macro.declaration_prompt_text(review),
        materials="\n\n".join([materials, *filter(None, tail)]),
    )
