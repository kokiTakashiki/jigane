"""方針のレビューに渡す正規表現の表と、実装とセキュリティの断片に渡す決定的な札。

正規表現の書き場所は言語アダプタの RegexAdapter が構文木から拾い、数えて札を付けるのは
`jigane.regex.tags.judge` が持つ。ここは変更した正規表現の選り分けと、
表と資料の行への並べ替えだけを持つ。
"""

from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from jigane.figures.models import RowChange, TableFigure, TableRow
from jigane.languages.base import RegexLiteral, RegexScan
from jigane.policy_materials import changed_files
from jigane.policy_materials.changed_files import MovedAwayFileChange
from jigane.regex.source import read_regex_source
from jigane.regex.tags import (
    RULE_DEFINITIONS,
    CountedRegex,
    CountedTag,
    RegexTag,
    RegexVerdict,
    Rule,
    ShapeTag,
    ShardGroup,
    UnreadableRegex,
    judge,
)
from jigane.reviews.lifecycle import Review

SECTION_REGEX = "正規表現の表"
INTRO = (
    "変更した正規表現を、jigane が構文から機械で数えた値と札で並べた。"
    "重みは正規表現の複雑さの合計で、札は規則（R1〜R7）の閾値を超えたものだけが付く。"
    "札の warning は対応を促す強さ、nit は任意の提案。"
    "本文は 60 文字で切ってあり、読めなかった正規表現は数えていない。"
)
# 表示はセルを折り返さないので、幅を取る本文を最後の列に置く
HEADER = ("位置", "書き場所", "重み", "札", "本文")
BODY_LIMIT = 60
NO_CHECKS = "なし"


def shard_group_of(prompt_name: str) -> ShardGroup | None:
    """断片のプロンプト名が属する群。札を渡さない群（知らない名前）には None。"""
    try:
        return ShardGroup(prompt_name)
    except ValueError:
        return None


# 規則の定義の資料用の直し方（material_direction）は句点で終わる。
# 注に並べるときは末尾の句点を落とす
DIRECTION_SEPARATOR = "。"


@dataclass(frozen=True)
class RegexSelection:
    """選り分けた正規表現。composed_count は、本文を数えられない書き場所の件数。"""

    targets: tuple[RegexLiteral, ...]
    composed_count: int


def _scan(root: Path, path: str) -> RegexScan | None:
    source = read_regex_source(root, path)
    return None if source is None else source.scan(path)


def select_regexes(review: Review) -> RegexSelection:
    """変更した正規表現。差分のレビューは、変更前の木の同じファイルに無い本文だけ。

    移したファイルは、変更前の木の移動元と比べる。
    """
    root = Path(review.repository_root)
    changes = changed_files.file_changes(review)
    # 鍵は変更後のパス。移したファイルの移動元の本文も、移動先のパスの鍵で持つ
    before: set[tuple[str, str]] = set()
    moved_composed: dict[str, int] = {}
    if review.is_diff and review.base_tree_path is not None:
        base_root = Path(review.base_tree_path)
        for path, entry in changed_files.file_changes(review, before=True).items():
            scan = _scan(base_root, path)
            if scan is None:
                continue
            after_path = entry.moved_to if isinstance(entry, MovedAwayFileChange) else path
            before.update((after_path, literal.pattern) for literal in scan.literals)
            if after_path != path:
                moved_composed[after_path] = len(scan.composed)
    targets: list[RegexLiteral] = []
    composed = 0
    for path in sorted(p for p, entry in changes.items() if entry.exists_after):
        scan = _scan(root, path)
        if scan is None:
            continue
        # 移したファイルは、移動元に在った組み立ての書き場所を足したものに数えない
        composed += max(0, len(scan.composed) - moved_composed.get(path, 0))
        targets += [
            literal
            for literal in scan.literals
            if not review.is_diff or (path, literal.pattern) not in before
        ]
    return RegexSelection(tuple(targets), composed)


def _cut(text: str) -> str:
    return text if len(text) <= BODY_LIMIT else text[:BODY_LIMIT] + "…"


def _short_name(tag: RegexTag) -> str:
    match tag:
        case CountedTag(rule=rule, count=count):
            return RULE_DEFINITIONS[rule].short_name.format(n=count)
        case ShapeTag(rule=rule):
            return RULE_DEFINITIONS[rule].short_name
        case _ as unreachable:
            assert_never(unreachable)


def _tags_text(verdict: RegexVerdict) -> str:
    match verdict:
        case UnreadableRegex(unreadable=unreadable):
            return f"読めない（{unreadable.reason}）"
        case CountedRegex(tags=tags):
            return "、".join(f"{tag.severity} {tag.rule} {_short_name(tag)}" for tag in tags)
        case _ as unreachable:
            assert_never(unreachable)


def _counted(verdicts: Iterable[RegexVerdict]) -> Iterator[CountedRegex]:
    """読めなかった判定は札を持たないので落とす。"""
    return (verdict for verdict in verdicts if isinstance(verdict, CountedRegex))


def _directions(verdicts: list[RegexVerdict]) -> str:
    """表に現れた規則ごとに一度だけ、直し方の向きを規則の順に一文ずつ句点で終えて並べる。

    表示は note の改行を効かせないので、改行に頼らず文で区切る。向きが無ければ空文字。
    """
    found: dict[Rule, str] = {}
    for verdict in _counted(verdicts):
        for tag in verdict.tags:
            direction = RULE_DEFINITIONS[tag.rule].material_direction.removesuffix(
                DIRECTION_SEPARATOR
            )
            # 表示は囲みを解釈せず文字のまま出すので、外す
            found.setdefault(tag.rule, direction.replace("`", ""))
    if not found:
        return ""
    return "直し方の向き: " + "".join(f"{rule} は、{found[rule]}。" for rule in sorted(found))


def _row(literal: RegexLiteral, verdict: RegexVerdict) -> TableRow:
    weight = str(verdict.metrics.complexity) if isinstance(verdict, CountedRegex) else ""
    cells = (
        f"{literal.path}:{literal.line}",
        literal.call,
        weight,
        _tags_text(verdict),
        _cut(literal.pattern),
    )
    # 選り分けた時点で変更した正規表現だけなので、行の変更の印は付けない
    return TableRow(cells, RowChange.UNCHANGED)


@dataclass(frozen=True)
class JudgedSelection:
    selection: RegexSelection
    verdicts: tuple[RegexVerdict, ...]


# 直近のレビュー一件の結果だけを持つ。断片ごとの呼び出しで選り分けと構文解析を繰り返さないため。
# 鍵はレビューの識別子、作成時刻、更新時刻、木の置き場で、更新すれば外れる
_last_judged: tuple[tuple[object, ...], JudgedSelection] | None = None


def judged_selection(review: Review) -> JudgedSelection:
    """変更した正規表現とその判定。レビューごとに一度だけ計算する。"""
    global _last_judged  # noqa: PLW0603 -- 直近一件だけを持つ。鍵が Review の複数の欄で、cache の引数にできない
    key = (
        review.id,
        review.created_at,
        review.refreshed_at,
        review.repository_root,
        review.base_tree_path,
    )
    if _last_judged is not None and _last_judged[0] == key:
        return _last_judged[1]
    selection = select_regexes(review)
    ordered = tuple(sorted(selection.targets, key=lambda literal: (literal.path, literal.line)))
    judged = JudgedSelection(
        RegexSelection(ordered, selection.composed_count),
        tuple(judge(literal) for literal in ordered),
    )
    _last_judged = (key, judged)
    return judged


def regex_table(review: Review) -> TableFigure | None:
    """変更した正規表現の表。変更が無く、組み立てた書き場所も無ければ None。"""
    judged = judged_selection(review)
    selection = judged.selection
    if not selection.targets and selection.composed_count == 0:
        return None
    note = _directions(list(judged.verdicts))
    if selection.composed_count:
        note += (
            f"値を文字列の定数に解けず数えなかった書き場所が {selection.composed_count} 件ある。"
        )
    rows = tuple(
        _row(literal, verdict)
        for literal, verdict in zip(selection.targets, judged.verdicts, strict=True)
    )
    return TableFigure(INTRO, HEADER, rows, note or None)


def shard_checks(prompt_name: str, review: Review, paths: Collection[str]) -> str:
    """断片のファイルにある札の一覧。群に渡す規則の札だけを、一件一行で並べる。

    札が無ければ NO_CHECKS の一語。
    """
    group = shard_group_of(prompt_name)
    judged = judged_selection(review)
    lines = []
    for literal, verdict in zip(judged.selection.targets, judged.verdicts, strict=True):
        if literal.path not in paths or isinstance(verdict, UnreadableRegex):
            continue
        for tag in verdict.tags:
            if group is not None and RULE_DEFINITIONS[tag.rule].shard_group is group:
                body = _cut(literal.pattern)
                lines.append(f"- {literal.path}:{literal.line} {tag.rule} {tag.severity} {body}")
    return "\n".join(lines) if lines else NO_CHECKS
