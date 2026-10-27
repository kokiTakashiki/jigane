"""`jigane design`: 設計書を読む部品の入口。いまは突き合わせるファイルの推しだけを持つ。"""

import argparse
import json
import re
from pathlib import Path
from typing import Any, assert_never

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.design.mentions import Mention, MentionKind, MentionVia
from jigane.design.parse import Section, parse_design
from jigane.design.recommend import (
    DEFAULT_LIMIT,
    AccessorCallerReason,
    Ambiguous,
    CalleeReason,
    CallerReason,
    Candidate,
    CandidateKind,
    Found,
    Location,
    LocationKind,
    Match,
    NamedReason,
    NotFound,
    ReaderReason,
    Reason,
    Recommendation,
    WriterReason,
    recommend,
)
from jigane.design.review_documents import attachment_candidates
from jigane.git import repository

_NUMBERED_TITLE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")
_VIA_PHRASE = {
    MentionVia.CLASS_DIAGRAM: "クラス図",
    MentionVia.ER_DIAGRAM: "ER 図",
    MentionVia.SEQUENCE_DIAGRAM: "シーケンス図",
    MentionVia.CODE_SPAN: "本文",
    MentionVia.TABLE_CODE_SPAN: "表",
    MentionVia.TABLE_CELL: "表",
    MentionVia.PROSE: "本文",
}


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"整数で指定してください: {text}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"1 以上の整数で指定してください: {text}")
    return value


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    design = subparsers.add_parser("design", help="設計書を読みます")
    actions = design.add_subparsers(dest="action", required=True, metavar="<action>")
    suggest = actions.add_parser(
        "suggest", help="設計書の名指しから、突き合わせるファイルを推します"
    )
    suggest.add_argument("design", help="設計書（Markdown）のパスです")
    suggest.add_argument(
        "--limit", type=_positive_int, default=DEFAULT_LIMIT, help="推しの上限です"
    )
    suggest.add_argument("--json", action="store_true")
    suggest.set_defaults(handler=run_suggest)


def _section_number(section: Section | None) -> str | None:
    """番号付きの題の番号。冒頭と番号の無い題は None。"""
    if section is None:
        return None
    numbered = _NUMBERED_TITLE.match(section.title + " ")
    return numbered[1] if numbered else None


def _section_label(section: Section | None) -> str:
    if section is None:
        return "冒頭"
    number = _section_number(section)
    return f"{number} 節" if number else f"「{section.title}」"


def _named_text(mention: Mention) -> str:
    label = _section_label(mention.section)
    phrase = _VIA_PHRASE[mention.via]
    name = mention.name
    match mention.kind:
        case MentionKind.TYPE | MentionKind.QUALIFIED_NAME:
            return f"{label}が{phrase}で `{name}` を名指しました"
        case MentionKind.PATH:
            return f"{label}が{phrase}で `{name}` のパスを名指しました"
        case MentionKind.SUBCOMMAND:
            return f"{label}の `jigane {name}` の副コマンドの実装"
        case MentionKind.OPTION:
            return f"{label}の `{name}` の引数を定義します"
        case MentionKind.STORED_FILE:
            return f"{label}の `{name}` を文字列の定数として持ちます"


def _reason_text(reason: Reason) -> str:
    name = reason.mention.name
    label = _section_label(reason.mention.section)
    match reason:
        case NamedReason():
            return _named_text(reason.mention)
        case CallerReason():
            return f"{label}が名指した `{name}` を呼ぶ側（一段のたどり）"
        case CalleeReason():
            return f"{label}が名指した `{name}` が呼ぶ先（一段のたどり）"
        case ReaderReason():
            return f"{label}が名指した保存物 `{name}` を読む関数（一段のたどり）"
        case WriterReason():
            return f"{label}が名指した保存物 `{name}` を書く関数（一段のたどり）"
        case AccessorCallerReason():
            return (
                f"{label}が名指した保存物 `{name}` を読み書きする関数 `{reason.function}` "
                "を呼ぶ側（二段のたどり）"
            )
        case _ as unreachable:
            assert_never(unreachable)


def _reason_dict(reason: Reason) -> dict[str, object]:
    base = {**_mention_dict(reason.mention), "trace": reason.trace.value}
    match reason:
        case NamedReason() | CallerReason() | CalleeReason():
            return base
        case ReaderReason() | WriterReason() | AccessorCallerReason():
            return {**base, "function": reason.function}
        case _ as unreachable:
            assert_never(unreachable)


_UNTRACKED_MARK = "追跡外"


def _candidate_lines(index: int, candidate: Candidate) -> list[str]:
    kind = {
        CandidateKind.DIRECT: "直接",
        CandidateKind.ONE_STEP: "一段のたどり",
        CandidateKind.TWO_STEP: "二段のたどり",
    }[candidate.kind]
    mark = f"、{_UNTRACKED_MARK}" if candidate.untracked else ""
    lines = [f"{index}. {candidate.path}（{kind}、{candidate.section_count} 節{mark}）"]
    lines.extend(f"   - {_reason_text(reason)}" for reason in candidate.reasons)
    return lines


def _location_text(location: Location) -> str:
    text = _location_core(location)
    return f"{text}（{_UNTRACKED_MARK}）" if location.untracked else text


def _location_core(location: Location) -> str:
    kind = location.kind
    match kind:
        case LocationKind.FILE | LocationKind.DIRECTORY:
            return f"{kind.value} {location.path}"
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
            return f"{kind.value} {location.path}:{location.line}"
        case _ as unreachable:
            assert_never(unreachable)


def _location_dict(location: Location) -> dict[str, Any]:
    return {
        "untracked": location.untracked,
        "path": location.path,
        "line": location.line,
        "label": location.label,
        "kind": location.kind.value,
    }


def _match_line(match: Match) -> str:
    mention = match.mention
    where = f"{mention.kind.value}、{_section_label(mention.section)}、{mention.line} 行"
    head = f"{mention.name}（{where}）"
    match match:
        case NotFound():
            return f"- {head}: 引き当たりませんでした"
        case Ambiguous():
            places = ", ".join(_location_text(loc) for loc in match.locations)
            return f"- {head}: 曖昧（候補 {places}）"
        case Found():
            places = ", ".join(_location_text(loc) for loc in match.locations)
            return f"- {head}: {places}"
        case _ as unreachable:
            assert_never(unreachable)


def render(recommendation: Recommendation) -> str:
    lines = ["推し"]
    for number, candidate in enumerate(recommendation.candidates, start=1):
        lines.extend(_candidate_lines(number, candidate))
    if not recommendation.candidates:
        lines.append("（候補なし）")
    lines.append(f"上限で切った候補: {recommendation.omitted} 件")
    lines += ["", "照合の表"]
    lines.extend(_match_line(match) for match in recommendation.matches)
    return "\n".join(lines)


def render_attachment_candidates(paths: list[str]) -> str:
    """付属の文書の候補。設計書のレビューを作る `new design-doc --with` で選ぶ。"""
    lines = ["付属の文書の候補（new design-doc --with で選ぶ）"]
    lines += [f"  {path}" for path in paths] or ["  （候補なし）"]
    return "\n".join(lines)


def _mention_dict(mention: Mention) -> dict[str, Any]:
    section = mention.section
    return {
        "name": mention.name,
        "mention_kind": mention.kind.value,
        "via": mention.via.value,
        "section": (
            None
            if section is None
            else {"number": _section_number(section), "title": section.title}
        ),
        "line": mention.line,
    }


def _match_dict(match: Match) -> dict[str, Any]:
    base = _mention_dict(match.mention)
    match match:
        case NotFound():
            return {**base, "status": "not_found", "resolved": False, "locations": []}
        case Ambiguous():
            return {
                **base,
                "status": "ambiguous",
                "resolved": False,
                "locations": [_location_dict(loc) for loc in match.locations],
            }
        case Found():
            return {
                **base,
                "status": "found",
                "resolved": True,
                "locations": [_location_dict(loc) for loc in match.locations],
            }
        case _ as unreachable:
            assert_never(unreachable)


def to_dict(recommendation: Recommendation) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "path": c.path,
                "kind": c.kind.value,
                "section_count": c.section_count,
                "untracked": c.untracked,
                "reasons": [_reason_dict(r) for r in c.reasons],
            }
            for c in recommendation.candidates
        ],
        "omitted": recommendation.omitted,
        "matches": [_match_dict(m) for m in recommendation.matches],
    }


def run_suggest(args: argparse.Namespace, context: Context) -> int:
    del context  # 推しは決定的な処理で、LLM を呼ばない
    document = parse_design(Path(args.design).read_text(encoding="utf-8"))
    root = repository.identify(Path.cwd()).root
    result = recommend(root, document, args.limit)
    candidates = attachment_candidates(root, Path(args.design).resolve().as_posix())
    if args.json:
        stored = to_dict(result)
        stored["attachment_candidates"] = candidates
        print(json.dumps(stored, ensure_ascii=False, indent=2))
    else:
        print(render(result))
        print()
        print(render_attachment_candidates(candidates))
    return EXIT_OK
