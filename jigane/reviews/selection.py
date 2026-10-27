"""最終的な指摘の記録（selection.json）。"""

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jigane.findings.discarded_nits import LEGACY_NOT_SELECTABLE, is_legacy_nit_id
from jigane.findings.records import (
    HistoryLoad,
    TriagedFinding,
    is_rejected,
    load_findings_state,
)
from jigane.groups.definitions import GROUP_IDS, GroupId
from jigane.reviews.lifecycle import ReviewError
from jigane.storage.store import review_lock, write_text_atomic
from jigane.storage.store_format import Upgrade, upgrade_mapping

SELECTION_FILE = "selection.json"
# selection.json の形の版。保存物の形を変えたら一つ上げ、_UPGRADES に前の版からの移しを足す
SELECTION_FORMAT = 3
_FINDING_PATTERN = re.compile(r"^F[1-9][0-9]*$")
_THEME_PATTERN = re.compile(r"^B[1-9][0-9]*$")


@dataclass(frozen=True)
class Picked:
    """最終的な指摘に入れる。"""


@dataclass(frozen=True)
class Held:
    """判断を保留にする。"""


@dataclass(frozen=True)
class Objected:
    """指摘に同意しない。理由を持つ。"""

    reason: str


SelectionVerdict = Picked | Held | Objected


def _number_order(ids: list[str]) -> list[str]:
    return sorted(ids, key=lambda text: int(text[1:]))


@dataclass
class Selection:
    """指摘の番号ごとの扱い。一つの番号は高々一つの扱いしか持たない。"""

    verdicts: dict[str, SelectionVerdict] = field(default_factory=dict)

    @property
    def picked(self) -> list[str]:
        return _number_order([n for n, v in self.verdicts.items() if isinstance(v, Picked)])

    @property
    def held(self) -> list[str]:
        return _number_order([n for n, v in self.verdicts.items() if isinstance(v, Held)])

    @property
    def objections(self) -> dict[str, str]:
        return {
            n: v.reason
            for n, v in sorted(self.verdicts.items(), key=lambda item: int(item[0][1:]))
            if isinstance(v, Objected)
        }

    def to_dict(self) -> dict[str, Any]:
        # --json の出力の契約
        return {"picked": self.picked, "held": self.held, "objections": self.objections}


def _dump_verdict(verdict: SelectionVerdict) -> dict[str, str]:
    match verdict:
        case Picked():
            return {"kind": "picked"}
        case Held():
            return {"kind": "held"}
        case Objected(reason):
            return {"kind": "objected", "reason": reason}


def _parse_verdict(number: str, raw: object) -> SelectionVerdict:
    kind = raw.get("kind") if isinstance(raw, Mapping) else None
    if kind == "picked":
        return Picked()
    if kind == "held":
        return Held()
    if kind == "objected" and isinstance(raw, Mapping) and isinstance(raw.get("reason"), str):
        return Objected(raw["reason"])
    raise ReviewError(f"{SELECTION_FILE} の指摘 {number} の扱いが読めません: {raw!r}")


def dump_selection(selection: Selection) -> dict[str, Any]:
    """selection.json へ書く境の一か所。最上位に今の版を刻む。"""
    return {
        "format": SELECTION_FORMAT,
        "verdicts": {n: _dump_verdict(v) for n, v in selection.verdicts.items()},
    }


def _upgrade_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 1 の selection.json を次の版へ移す。

    版 1 は版 2 と鍵も値も同じで、版の鍵が無いだけである。
    """
    return copy.deepcopy(dict(raw))


def _upgrade_v2(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 2 の selection.json を次の版へ移す。

    三つの集まりを、番号ごとに一つの扱いへ畳む。二つ以上の扱いに入っている番号は
    どれを残すか決められないので、保存物が読めない誤りとして止める。
    """
    held = {number: {"kind": "held"} for number in raw["held"]}
    picked = {number: {"kind": "picked"} for number in raw["picked"]}
    objected = {
        number: {"kind": "objected", "reason": reason}
        for number, reason in raw["objections"].items()
    }
    verdicts: dict[str, dict[str, str]] = {}
    for number in {*picked, *held, *objected}:
        kinds = [
            name
            for name, group in (("picked", picked), ("held", held), ("objections", objected))
            if number in group
        ]
        if len(kinds) > 1:
            raise ReviewError(
                f"{SELECTION_FILE} の指摘 {number} が複数の扱いに入っています: {', '.join(kinds)}"
            )
        verdicts[number] = (picked | held | objected)[number]
    return {"verdicts": verdicts}


# 保存された版から次の版への移し。parse_selection が SELECTION_FORMAT まで順に回す
_UPGRADES: dict[int, Upgrade] = {1: _upgrade_v1, 2: _upgrade_v2}


def parse_selection(raw: Mapping[str, Any]) -> Selection:
    """selection.json を読む境の一か所。保存された版から今の版まで移してから型へ読む。"""
    stored = upgrade_mapping(SELECTION_FILE, raw, current=SELECTION_FORMAT, upgrades=_UPGRADES)
    return Selection({n: _parse_verdict(n, v) for n, v in stored["verdicts"].items()})


def load_selection(directory: Path) -> Selection:
    path = directory / SELECTION_FILE
    if not path.is_file():
        return Selection()
    return parse_selection(json.loads(path.read_text(encoding="utf-8")))


def save_selection(directory: Path, selection: Selection) -> None:
    write_text_atomic(
        directory / SELECTION_FILE,
        json.dumps(dump_selection(selection), ensure_ascii=False, indent=2),
    )


def current_findings(directory: Path) -> dict[str, TriagedFinding]:
    """今の結果にある指摘を、F の番号で引けるようにしたもの。"""
    state = load_findings_state(directory)
    return {f.finding.id: f for findings in state.groups.values() for f in findings}


def current_ids(directory: Path) -> set[str]:
    """今の結果にある指摘の F の番号。"""
    state = load_findings_state(directory)
    return {f.finding.id for findings in state.groups.values() for f in findings}


def stale_numbers(directory: Path, selection: Selection) -> list[str]:
    """再実行で番号が新しくなり、今の指摘に無くなった F の番号。"""
    known = current_ids(directory)
    return _number_order([n for n in selection.verdicts if n not in known])


@dataclass(frozen=True)
class StaleFinding:
    """今回の結果に無い選択済みの番号。履歴から本文を引けたら、前の回の指摘の詳細を持つ。"""

    id: str
    past: "PastFinding | None"

    def to_dict(self) -> dict[str, Any]:
        """画面へ渡す形。履歴に無い番号は id と group だけ。"""
        if self.past is None:
            return {"id": self.id, "group": None}
        past = self.past
        return {
            "id": self.id,
            "group": past.group,
            "ran_at": past.ran_at,
            "severity": past.severity,
            "viewpoint": past.viewpoint,
            "claim": past.claim,
            "body": past.body,
            "evidence": past.evidence,
        }

    @property
    def in_history(self) -> bool:
        return self.past is not None

    def text(self) -> str:
        """CLI の表示に使う一行。本文を引けたものは前の回の指摘と分かる形にする。"""
        if self.past is None:
            return f"{self.id}（履歴にありません）"
        head = f"{self.id} {self.past.viewpoint} {self.past.claim}"
        text = f"{head}（前の回の指摘で、今回の結果にありません）"
        return f"{text}\n{self.past.body}" if self.past.body else text


@dataclass(frozen=True)
class PastFinding:
    """履歴から引いた前の回の指摘。どの群の、どの回のものかを添える。"""

    group: GroupId
    ran_at: str | None
    severity: str
    viewpoint: str
    claim: str
    body: str | None
    evidence: Any


def history_findings(history: HistoryLoad) -> dict[str, PastFinding]:
    """履歴にある指摘を F の番号で引けるようにしたもの。

    history は読み済みの履歴。履歴を読むのは入口の一か所で、ここでは読まない。
    """
    found: dict[str, PastFinding] = {}
    for group in GROUP_IDS:
        for entry in reversed(history.rounds.get(group, [])):
            for finding in entry.findings:
                found.setdefault(
                    finding.body.id,
                    PastFinding(
                        group=group,
                        ran_at=entry.ran_at,
                        severity=finding.body.severity,
                        viewpoint=finding.body.viewpoint,
                        claim=finding.body.claim,
                        body=finding.body.extra.get("body"),
                        evidence=finding.body.extra["evidence"],
                    ),
                )
    return found


def stale_details(
    directory: Path,
    selection: Selection,
    history: HistoryLoad,
) -> list[StaleFinding]:
    """stale_numbers の各番号を、履歴から引いた本文に結ぶ。履歴に無い番号は past が None。

    本文を引けたものは、前の回の指摘で今回の結果に無いもの。
    """
    past = history_findings(history)
    return [
        StaleFinding(number, past.get(number)) for number in stale_numbers(directory, selection)
    ]


def _unknown_message(tokens: list[str]) -> str:
    """番号を受け付けない誤りの文。古い記録の候補の番号は、知らないのでなく選べないと分けて言う。"""
    legacy = [t for t in tokens if is_legacy_nit_id(t)]
    unknown = [t for t in tokens if not is_legacy_nit_id(t)]
    parts: list[str] = []
    if legacy:
        parts.append(f"{LEGACY_NOT_SELECTABLE}: {', '.join(legacy)}")
    if unknown:
        parts.append(f"知らない指摘の番号です: {', '.join(unknown)}")
    return "。".join(parts)


def _split_tokens(directory: Path, tokens: list[str]) -> tuple[list[str], list[str]]:
    """番号を F の番号の列と、B の所属の F の番号の列に分ける。知らない番号は ReviewError にする。

    F の番号に直接挙げられた番号は、B の所属にも入っていても前者に入れる。
    """
    findings = current_ids(directory)
    themes = {t.id: t for t in load_findings_state(directory).themes}
    direct: dict[str, None] = {}
    members: dict[str, None] = {}
    unknown: list[str] = []
    for token in tokens:
        if _FINDING_PATTERN.match(token) and token in findings:
            direct[token] = None
        elif _THEME_PATTERN.match(token) and token in themes:
            for member in themes[token].finding_ids:
                members[member] = None
        else:
            unknown.append(token)
    if unknown:
        raise ReviewError(_unknown_message(unknown))
    return list(direct), [m for m in members if m not in direct]


def expand(directory: Path, tokens: list[str]) -> list[str]:
    """F と B の番号を F の番号の列に展開する。知らない番号は ReviewError にする。"""
    direct, members = _split_tokens(directory, tokens)
    return [*direct, *members]


@dataclass(frozen=True)
class Removal:
    """un- 操作の結果。skipped は B の所属のうち、扱いが違って外さなかった F の番号。"""

    removed: tuple[str, ...]
    skipped: tuple[str, ...]
    via_theme: bool


@dataclass(frozen=True)
class LostReason:
    """置き換えで消えた異議の理由。"""

    number: str
    reason: str


def _replace(selection: Selection, ids: list[str], verdict: SelectionVerdict) -> list[LostReason]:
    lost: list[LostReason] = []
    for number in ids:
        previous = selection.verdicts.get(number)
        if isinstance(previous, Objected):
            lost.append(LostReason(number, previous.reason))
        selection.verdicts[number] = verdict
    return lost


def _remove(
    directory: Path, selection: Selection, tokens: list[str], kind: type, mismatch: str
) -> Removal:
    """扱いが kind の番号だけを外す。

    mismatch は誤りの文の頭。F の番号は一つでも扱いが違えば何も外さず ReviewError にする。
    B の所属は扱いが合うものだけを外し、一件も外せなければ ReviewError にする。
    """
    direct, members = _split_tokens(directory, tokens)
    wrong = [n for n in direct if not isinstance(selection.verdicts.get(n), kind)]
    if wrong:
        raise ReviewError(f"{mismatch}: {', '.join(wrong)}")
    matching = [n for n in members if isinstance(selection.verdicts.get(n), kind)]
    skipped = [n for n in members if n not in matching]
    if members and not matching and not direct:
        raise ReviewError(f"{mismatch}: {', '.join(skipped)}")
    for number in [*direct, *matching]:
        del selection.verdicts[number]
    return Removal((*direct, *matching), tuple(skipped), via_theme=bool(members))


def pick(directory: Path, tokens: list[str]) -> tuple[Selection, list[str], list[LostReason]]:
    """前の扱いを picked に置き換える。

    二つ目は棄却済みなのに pick した F の番号、三つ目は置き換えで消えた異議の理由。
    """
    with review_lock(directory):
        ids = expand(directory, tokens)
        findings = current_findings(directory)
        selection = load_selection(directory)
        lost = _replace(selection, ids, Picked())
        save_selection(directory, selection)
        return selection, [i for i in ids if is_rejected(findings[i].finding)], lost


def unpick(directory: Path, tokens: list[str]) -> tuple[Selection, Removal]:
    with review_lock(directory):
        selection = load_selection(directory)
        removal = _remove(directory, selection, tokens, Picked, "pickedではない指摘の番号です")
        save_selection(directory, selection)
        return selection, removal


def hold(directory: Path, tokens: list[str]) -> tuple[Selection, list[LostReason]]:
    with review_lock(directory):
        ids = expand(directory, tokens)
        selection = load_selection(directory)
        lost = _replace(selection, ids, Held())
        save_selection(directory, selection)
        return selection, lost


def unhold(directory: Path, tokens: list[str]) -> tuple[Selection, Removal]:
    with review_lock(directory):
        selection = load_selection(directory)
        removal = _remove(directory, selection, tokens, Held, "heldではない指摘の番号です")
        save_selection(directory, selection)
        return selection, removal


def object_to(directory: Path, token: str, reason: str) -> tuple[Selection, list[LostReason]]:
    with review_lock(directory):
        if not reason.strip():
            raise ReviewError("異議の理由が空です")
        if not _FINDING_PATTERN.match(token) or token not in current_ids(directory):
            raise ReviewError(_unknown_message([token]))
        selection = load_selection(directory)
        lost = _replace(selection, [token], Objected(reason))
        save_selection(directory, selection)
        return selection, lost


def unobject(directory: Path, token: str) -> tuple[Selection, Removal]:
    with review_lock(directory):
        selection = load_selection(directory)
        removal = _remove(directory, selection, [token], Objected, "異議の無い指摘の番号です")
        save_selection(directory, selection)
        return selection, removal


def summary(directory: Path, history: HistoryLoad) -> dict[str, Any]:
    """show の概要と --json に出す、選択の今の状態。history は読み済みの履歴。"""
    selection = load_selection(directory)
    return {
        **selection.to_dict(),
        "stale": stale_numbers(directory, selection),
        "stale_details": [
            detail.to_dict() for detail in stale_details(directory, selection, history)
        ],
    }
