"""返信とレポートのテンプレートの探索と、最終的な指摘の流し込み。"""

import re
import traceback
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import FunctionLoader, StrictUndefined, TemplateNotFound
from jinja2.exceptions import TemplateError, TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment

from jigane.findings.discard import (
    DISCARDED_VIEWPOINT,
    NO_CONCERN_CLAIM,
    concern_with_places,
    places_line,
)
from jigane.findings.records import (
    Adjudication,
    FindingBody,
    FindingKind,
    ScopeBody,
    TriagedFinding,
    load_findings_state,
    load_history_checked,
    triage_sort_key,
)
from jigane.gates.entry import Allowed, GateEntry, Gates, GateSource
from jigane.groups.definitions import GROUP_BY_ID, MACRO, MICRO, SECURITY
from jigane.reviews import selection as selection_module
from jigane.reviews.lifecycle import PullRequestOrigin, Review, ReviewError
from jigane.storage.store import data_home

SUFFIX = ".md.j2"
BUNDLED = "bundled"
USER = "user"
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# CommonMark の仕様の Backslash escapes の節は、ASCII の句読点を逆スラッシュで逃がせるとする。
# 逃がしすぎると生のテキストを読む人の妨げになるため、行の途中で記法になりうるものに絞る。
#   \ ` * _ ~    : 逃がしの記号そのもの、コード、強調、取り消し線
#   [ ] !         : リンク、参照リンク（MD052 の原因）、画像
#   < >           : 自動リンク、生の HTML、引用
#   # |           : 見出し、表の区切り
#   &             : 文字参照
# 括弧、引用符、`.`、`:` などは、上の記号と組にならなければ記法にならないため逃がさない。
_INLINE_SPECIALS = re.compile(r"[\\`*_~\[\]!<>#|&]")
# 指摘の本文は箇条の中に字下げして流し込まれるため、行頭の記号は入れ子の箇条や水平線、
# 見出しの下線になる。行頭の `-` `+` `=` と、番号付きの箇条の `1.` `1)` も逃がす。
_LINE_START = re.compile(r"^([ \t]*)(?:([-+=])|(\d+)([.)])(?=[ \t]|$))", re.MULTILINE)

# 群の並びは方針、実装、テスト規約、セキュリティ
GROUP_LABELS = {
    MACRO: GROUP_BY_ID[MACRO].label,
    MICRO: GROUP_BY_ID[MICRO].label,
    "test": "テスト規約",
    SECURITY: GROUP_BY_ID[SECURITY].label,
}


class Escaped(str):
    """逃がし済みの文字列。フィルタを重ねて当てても、逃がしが二重にならない。"""

    __slots__ = ()


def escape_markdown(value: object) -> Escaped:
    if isinstance(value, Escaped):
        return value
    text = _INLINE_SPECIALS.sub(lambda m: "\\" + m.group(0), str(value))
    return Escaped(
        _LINE_START.sub(
            lambda m: (
                m.group(1) + (f"\\{m.group(2)}" if m.group(2) else f"{m.group(3)}\\{m.group(4)}")
            ),
            text,
        )
    )


@dataclass(frozen=True)
class TemplateEntry:
    name: str
    origin: str
    path: Path

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "origin": self.origin, "path": str(self.path)}


def user_directory() -> Path:
    return data_home() / "templates"


def _bundled_directory() -> Path:
    return Path(str(resources.files("jigane") / "templates"))


def _names_in(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        return {}
    return {p.name.removesuffix(SUFFIX): p for p in directory.glob(f"*{SUFFIX}") if p.is_file()}


def list_templates() -> list[TemplateEntry]:
    """名前ごとに実際に使われる一つを、名前順に返す。同じ名前なら利用者のものが勝つ。"""
    found = {n: TemplateEntry(n, BUNDLED, p) for n, p in _names_in(_bundled_directory()).items()}
    found.update({n: TemplateEntry(n, USER, p) for n, p in _names_in(user_directory()).items()})
    return [found[name] for name in sorted(found)]


def find_template(name: str) -> TemplateEntry:
    if not _NAME_PATTERN.match(name):
        raise ReviewError(f"テンプレートの名前が不正です: {name!r}")
    for entry in list_templates():
        if entry.name == name:
            return entry
    raise ReviewError(f"テンプレートがありません: {name}（jigane templates で一覧を見られます）")


def _environment() -> SandboxedEnvironment:
    def load(path: str) -> tuple[str, str, None] | None:
        file = Path(path)
        if not file.is_file():
            return None
        return file.read_text(encoding="utf-8"), str(file), None

    # 出力は Markdown の文面であり、HTML のエスケープをかけると指摘の本文の記号が壊れる
    environment = SandboxedEnvironment(
        loader=FunctionLoader(load),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # 指摘の文字列が Markdown の記法として解釈されないよう、テンプレートから当てる
    environment.filters["md"] = escape_markdown
    return environment


def render(entry: TemplateEntry, context: dict[str, Any]) -> str:
    try:
        return _environment().get_template(str(entry.path)).render(context)
    except TemplateSyntaxError as error:
        raise ReviewError(
            f"テンプレート {entry.name}（{entry.path}）の {error.lineno} 行目が構文の誤りです: "
            f"{error.message}"
        ) from error
    except TemplateNotFound as error:
        raise ReviewError(f"テンプレートが読めません: {entry.path}") from error
    except TemplateError as error:
        raise ReviewError(
            f"テンプレート {entry.name}（{entry.path}）の{_line_of(error, entry.path)}で"
            f"失敗しました: {error}"
        ) from error


def _line_of(error: BaseException, path: Path) -> str:
    lines = [
        f.lineno for f in traceback.extract_tb(error.__traceback__) if f.filename == str(path)
    ]
    return f" {lines[-1]} 行目" if lines else ""


def _location(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        **evidence,
        "location": f"{evidence['path']}:{evidence['start_line']}-{evidence['end_line']}",
    }


def _adjudication(adjudication: Adjudication | None) -> dict[str, Any] | None:
    if adjudication is None:
        return None
    return {
        "kind": adjudication.kind.value,
        "verdict": adjudication.verdict.value,
        "reason": adjudication.reason,
    }


def _sort_key(finding: TriagedFinding) -> tuple[int, int]:
    return triage_sort_key(finding), int(finding.finding.id[1:])


def _places_line_without_concern(body: FindingBody) -> str | None:
    if body.nit_reason is None or concern_with_places(body.nit_reason) is not None:
        return None
    return places_line(body.nit_reason)


def _finding_context(finding: TriagedFinding, theme_title: str | None) -> dict[str, Any]:
    """テンプレートに渡す指摘の文脈を、型から組む唯一の場所。"""
    scope_reason: str | None = None
    if isinstance(finding.finding, ScopeBody):
        scope_reason = str(finding.finding.out_of_scope["reason"])
    body = finding.finding
    deepens = body.continued_from_value()
    return {
        "number": body.id,
        "group": body.extra["group"],
        # 捨てた候補から写した nit の観点は機械が読む印で、利用者の雛形には渡さない
        "viewpoint": "" if body.viewpoint == DISCARDED_VIEWPOINT else body.viewpoint,
        "severity": body.severity,
        "claim": (concern_with_places(body.nit_reason) or NO_CONCERN_CLAIM)
        if body.nit_reason is not None
        else body.claim,
        # 画面と同じ開示の場所の行。懸念の文が無い nit にだけ付け、claim は変えない
        "places_line": _places_line_without_concern(body),
        "body": body.extra.get("body", ""),
        "evidence": [_location(e) for e in body.extra["evidence"]],
        "adjudication": _adjudication(body.adjudication),
        "theme": theme_title,
        # 必読と分けて書く指摘の種類。範囲外は理由を、深掘りは前の回の番号を添える
        "kind": body.kind,
        "scope_reason": scope_reason,
        "deepens": deepens,
    }


def _group_context(
    group: str, items: list[dict[str, Any]], titles: dict[str, str]
) -> dict[str, Any]:
    by_theme: dict[str | None, list[dict[str, Any]]] = {}
    for item in items:
        by_theme.setdefault(item["theme_id"], []).append(item["finding"])
    themes: list[dict[str, Any]] = [
        {"id": theme_id, "title": titles[theme_id], "findings": members}
        for theme_id, members in by_theme.items()
        if theme_id is not None
    ]
    if None in by_theme:
        # テーマに属さない指摘は、題の無い束として末尾に置く
        themes.append({"id": None, "title": None, "findings": by_theme[None]})
    findings = [item["finding"] for item in items]
    return {
        "group": group,
        "label": GROUP_LABELS[group],
        "findings": findings,
        # 通常の指摘のあとに、種類の宣言の順で節を分ける種類の指摘を並べる
        "regular": [f for f in findings if f["kind"] is FindingKind.REGULAR],
        "kind_sections": [
            {
                "heading": kind.traits.heading,
                "findings": [f for f in findings if f["kind"] is kind],
            }
            for kind in FindingKind
            if kind.traits.heading is not None
        ],
        "themes": themes,
    }


def _gate_context(entry: GateEntry) -> dict[str, Any]:
    """テンプレートが引く欄。強さと、注記で外した理由を返信に出すのは正規表現の札だけ。"""
    return {
        "number": entry.number,
        "path": entry.path,
        "line": entry.line,
        "explanation": entry.explanation,
        "severity": str(entry.severity) if entry.source is GateSource.REGEX else None,
        "allowed": entry.state.reason if isinstance(entry.state, Allowed) else None,
    }


def _pull_request(review: Review) -> dict[str, Any] | None:
    origin = review.origin
    if not isinstance(origin, PullRequestOrigin):
        # PR の情報は PR の出所だけが持つ。範囲と機能は null
        return None
    info = origin.info
    return {
        "url": info.url,
        "number": info.number,
        "title": info.title,
    }


def build_context(review: Review) -> tuple[dict[str, Any], list[str]]:
    """テンプレートに流し込む値と、stderr に示す警告。同じ入力から同じ結果を返す。

    載せるのは利用者が下書きに入れた（picked）指摘だけである。捨てた候補から写した nit も、
    保存した指摘なら下書きに入れたものが載る。写す前の古い記録の候補（D- の番号）は
    保存した指摘に無く選べないので、ここには来ない。
    """
    directory = review.id.directory()
    selection = selection_module.load_selection(directory)
    known = selection_module.current_findings(directory)
    state = load_findings_state(directory)
    titles = {t.id: t.title for t in state.themes}
    history = load_history_checked(directory)
    warnings = [f"警告: {warning.text()}" for warning in history.warnings]
    warnings += [
        f"注: 今の指摘に無い番号を最終的な指摘から除きました: {detail.text()}"
        for detail in selection_module.stale_details(directory, selection, history)
        if detail.id in selection.picked
    ]
    picked = [known[n] for n in selection.picked if n in known]
    grouped: dict[str, list[dict[str, Any]]] = {g: [] for g in GROUP_LABELS}
    for finding in sorted(picked, key=_sort_key):
        theme_id = finding.finding.theme
        grouped[finding.finding.extra["group"]].append(
            {
                "theme_id": theme_id if theme_id in titles else None,
                "finding": _finding_context(finding, titles.get(theme_id or "")),
            }
        )
    groups = [_group_context(g, items, titles) for g, items in grouped.items() if items]
    themes = [t for g in groups for t in g["themes"] if t["id"] is not None]
    gates = review.gates or Gates()
    context = {
        "review": {"id": str(review.id), "mode": review.mode, "warnings": review.warnings},
        "pull_request": _pull_request(review),
        "declaration": {
            "source": review.declaration.source.value,
            "intent": review.declaration.intent,
        },
        "findings": [f for g in groups for f in g["findings"]],
        "groups": groups,
        "themes": themes,
        "gates": {
            "violations": [_gate_context(entry) for entry in gates.violations],
            "counted": gates.counted_count,
            "warnings": [_gate_context(entry) for entry in gates.warnings],
        },
    }
    return context, warnings
