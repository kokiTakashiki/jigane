"""最終的な指摘の記録（selection.json）。"""

import copy
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from jigane.findings.discarded_nits import LEGACY_NOT_SELECTABLE, is_legacy_nit_id
from jigane.findings.records import (
    FindingBody,
    HistoryLoad,
    StoredTheme,
    TriagedFinding,
    is_rejected,
    load_findings_state,
)
from jigane.groups.definitions import GROUP_IDS, GroupId
from jigane.groups.viewpoints import viewpoint_fields
from jigane.reviews.lifecycle import ReviewError
from jigane.storage.atomic_write import write_text_atomic
from jigane.storage.locking import review_lock
from jigane.storage.store_format import Upgrade, upgrade_mapping

SELECTION_FILE = "selection.json"
# selection.json の形の版。保存物の形を変えたら一つ上げ、_UPGRADES に前の版からの移しを足す
SELECTION_FORMAT = 4
_FINDING_PATTERN = re.compile(r"^F[1-9][0-9]*$")
_THEME_PATTERN = re.compile(r"^B[1-9][0-9]*$")


@dataclass(frozen=True)
class PastRoundInputs:
    """前の回の指摘を照合するために読む、履歴と、群ごとの今の結果を作った実行の時刻。"""

    history: HistoryLoad
    current_ran_at: Mapping[GroupId, str | None]


# 読む関数は入口が組み立てる。runs.json の読み口は reviews 層から import できない
PastRoundReader = Callable[[], PastRoundInputs]


class NoRanAt(Enum):
    """回の記録が実行時刻を持たない（時刻を残さない古い回）ことを表す、ただ一つの値。"""

    ABSENT = "no_ran_at"


class UnknownOrigin(Enum):
    """出所を持たない版 3 から移した扱いであることを表す、ただ一つの値。"""

    ABSENT = "unknown_origin"


# 呼ぶ側が使う名前はこの二つだけ。列挙の値は直接参照しない
NO_RAN_AT = NoRanAt.ABSENT
UNKNOWN_ORIGIN = UnknownOrigin.ABSENT

RanAt = str | NoRanAt


@dataclass(frozen=True)
class RoundOrigin:
    """指摘が属する回の出所。群と、その回を作った実行の時刻。時刻の記録が無い回は NO_RAN_AT。"""

    group: GroupId
    ran_at: RanAt

    @classmethod
    def recorded(cls, group: GroupId, ran_at: str | None) -> "RoundOrigin":
        """保存や履歴から読んだ時刻（無ければ None）を、名前の付いた値へ写して作る唯一の口。"""
        match ran_at:
            case str():
                return cls(group, ran_at)
            case None:
                return cls(group, NO_RAN_AT)


SelectionOrigin = RoundOrigin | UnknownOrigin


@dataclass(frozen=True)
class Picked:
    """最終的な指摘に入れる。origin が UNKNOWN_ORIGIN なのは、版 3 から移した扱い。"""

    origin: SelectionOrigin = UNKNOWN_ORIGIN


@dataclass(frozen=True)
class Held:
    """判断を保留にする。origin が UNKNOWN_ORIGIN なのは、出所を持たない版 3 から移した扱い。"""

    origin: SelectionOrigin = UNKNOWN_ORIGIN


@dataclass(frozen=True)
class Objected:
    """指摘に同意しない。理由を持つ。origin が UNKNOWN_ORIGIN なのは、版 3 から移した扱い。"""

    reason: str
    origin: SelectionOrigin = UNKNOWN_ORIGIN


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


def _dump_ran_at(ran_at: RanAt) -> str | None:
    match ran_at:
        case str():
            return ran_at
        case NoRanAt():
            return None


def _dump_origin(origin: SelectionOrigin) -> dict[str, str | None] | None:
    match origin:
        case RoundOrigin(group, ran_at):
            return {"group": group.value, "ran_at": _dump_ran_at(ran_at)}
        case UnknownOrigin():
            return None


def _dump_verdict(verdict: SelectionVerdict) -> dict[str, Any]:
    match verdict:
        case Picked(origin):
            return {"kind": "picked", "round": _dump_origin(origin)}
        case Held(origin):
            return {"kind": "held", "round": _dump_origin(origin)}
        case Objected(reason, origin):
            return {"kind": "objected", "reason": reason, "round": _dump_origin(origin)}


def _parse_origin(number: str, raw: object) -> SelectionOrigin:
    if raw is None:
        return UNKNOWN_ORIGIN
    if isinstance(raw, Mapping) and raw.get("group") in GROUP_IDS:
        ran_at = raw.get("ran_at")
        if ran_at is None or isinstance(ran_at, str):
            return RoundOrigin.recorded(GroupId(raw["group"]), ran_at)
    raise ReviewError(f"{SELECTION_FILE} の指摘 {number} の回の出所が読めません: {raw!r}")


def _parse_verdict(number: str, raw: object) -> SelectionVerdict:
    kind = raw.get("kind") if isinstance(raw, Mapping) else None
    if isinstance(raw, Mapping) and "round" in raw:
        origin = _parse_origin(number, raw["round"])
        if kind == "picked":
            return Picked(origin)
        if kind == "held":
            return Held(origin)
        if kind == "objected" and isinstance(raw.get("reason"), str):
            return Objected(raw["reason"], origin)
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


def _upgrade_v3(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 3 の selection.json を次の版へ移す。

    扱いはそのまま写す。版 3 は出所を持たないので、出所の項目そのものを null（不明）にする。
    """
    return {
        "verdicts": {
            number: {**verdict, "round": None} if isinstance(verdict, Mapping) else verdict
            for number, verdict in raw["verdicts"].items()
        }
    }


# 保存された版から次の版への移し。parse_selection が SELECTION_FORMAT まで順に回す
_UPGRADES: dict[int, Upgrade] = {1: _upgrade_v1, 2: _upgrade_v2, 3: _upgrade_v3}


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
        origin, body = self.past.origin, self.past.body
        return {
            "id": self.id,
            "group": origin.group,
            "ran_at": _dump_ran_at(origin.ran_at),
            "severity": body.severity,
            "viewpoint": body.viewpoint,
            **viewpoint_fields(body.viewpoint),
            "claim": body.claim,
            "body": body.extra.get("body"),
            "evidence": body.extra["evidence"],
        }

    @property
    def in_history(self) -> bool:
        return self.past is not None

    def text(self) -> str:
        """CLI の表示に使う一行。本文を引けたものは前の回の指摘と分かる形にする。"""
        if self.past is None:
            return f"{self.id}（履歴にありません）"
        body = self.past.body
        head = f"{self.id} {body.viewpoint} {body.claim}"
        text = f"{head}（前の回の指摘で、今回の結果にありません）"
        detail = body.extra.get("body")
        return f"{text}\n{detail}" if detail else text


@dataclass(frozen=True)
class PastFinding:
    """履歴から引いた前の回の指摘。どの群の、どの回のものかを添える。"""

    origin: RoundOrigin
    body: FindingBody


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
                    PastFinding(RoundOrigin.recorded(group, entry.ran_at), finding.body),
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


_HISTORY_UNREADABLE_NOTE = "（履歴が読めないため、前の回の指摘の番号は受け付けられません）"


@dataclass(frozen=True)
class _Known:
    """受け付ける F の番号。今の結果と履歴の和で、番号ごとに回の出所を持つ。

    places は番号ごとの出所の場所の表示。二か所以上にある番号は、使われたときだけ止める。
    """

    origins: dict[str, RoundOrigin]
    places: dict[str, tuple[str, ...]]
    current: dict[str, TriagedFinding]
    themes: dict[str, StoredTheme]
    history_readable: bool


def _require_unique(known: _Known, numbers: Iterable[str]) -> None:
    """指定された番号が二か所以上に現れていたら、保存物の不整合として止める。"""
    for number in numbers:
        places = known.places[number]
        if len(places) > 1:
            raise ReviewError(
                f"保存物の不整合です: 指摘 {number} が {' と '.join(places)} の複数か所にあります"
            )


def _unknown_error(known: _Known, tokens: list[str]) -> ReviewError:
    """番号を受け付けない誤り。古い記録の候補の番号は、知らないのでなく選べないと分けて言う。"""
    legacy = [t for t in tokens if is_legacy_nit_id(t)]
    unknown = [t for t in tokens if not is_legacy_nit_id(t)]
    parts: list[str] = []
    if legacy:
        parts.append(f"{LEGACY_NOT_SELECTABLE}: {', '.join(legacy)}")
    if unknown:
        note = "" if known.history_readable else _HISTORY_UNREADABLE_NOTE
        parts.append(f"知らない指摘の番号です: {', '.join(unknown)}{note}")
    return ReviewError("。".join(parts))


def _locate(directory: Path, inputs: PastRoundInputs) -> _Known:
    """今の結果と履歴の番号を集める。二か所に現れた番号は、場所を覚えておく。

    inputs はレビューの錠の中で読み関数から受け取った、履歴と群ごとの今の結果の実行時刻。
    """
    history, current_ran_at = inputs.history, inputs.current_ran_at
    state = load_findings_state(directory)
    origins: dict[str, RoundOrigin] = {}
    places: dict[str, tuple[str, ...]] = {}
    current: dict[str, TriagedFinding] = {}

    def claim(number: str, origin: RoundOrigin, place: str) -> None:
        origins.setdefault(number, origin)
        places[number] = (*places.get(number, ()), place)

    for group, findings in state.groups.items():
        origin = RoundOrigin.recorded(group, current_ran_at.get(group))
        for triaged in findings:
            claim(triaged.finding.id, origin, f"今の結果（{group.value}）")
            current[triaged.finding.id] = triaged
    for group in GROUP_IDS:
        for entry in history.rounds.get(group, []):
            origin = RoundOrigin.recorded(group, entry.ran_at)
            for stored in entry.findings:
                claim(stored.body.id, origin, f"履歴（{group.value}、{entry.ran_at}）")
    themes = {t.id: t for t in state.themes}
    return _Known(origins, places, current, themes, history_readable=not history.warnings)


def _split_tokens(known: _Known, tokens: list[str]) -> tuple[list[str], list[str]]:
    """番号を F の番号の列と、B の所属の F の番号の列に分ける。知らない番号は ReviewError にする。

    F の番号に直接挙げられた番号は、B の所属にも入っていても前者に入れる。
    B の番号は今の結果のまとめだけで展開する。
    """
    direct: dict[str, None] = {}
    members: dict[str, None] = {}
    unknown: list[str] = []
    for token in tokens:
        if _FINDING_PATTERN.match(token) and token in known.origins:
            direct[token] = None
        elif _THEME_PATTERN.match(token) and token in known.themes:
            for member in known.themes[token].finding_ids:
                members[member] = None
        else:
            unknown.append(token)
    if unknown:
        raise _unknown_error(known, unknown)
    members_only = [m for m in members if m not in direct]
    _require_unique(known, [*direct, *members_only])
    return list(direct), members_only


def _expand(known: _Known, tokens: list[str]) -> list[str]:
    """F と B の番号を F の番号の列に展開する。知らない番号は ReviewError にする。"""
    direct, members = _split_tokens(known, tokens)
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


_KindOfVerdict = type[Picked] | type[Held] | type[Objected]


@dataclass(frozen=True)
class _KindSpec:
    """扱いの種類ごとの作り方と、外せない番号を拒むときの文の頭。"""

    build: Callable[[RoundOrigin, str], SelectionVerdict]
    mismatch_head: str


# reason を使うのは Objected だけ。他の作り方は受け取って捨てる
_KINDS: dict[_KindOfVerdict, _KindSpec] = {
    Picked: _KindSpec(lambda origin, _reason: Picked(origin), "pickedではない指摘の番号です"),
    Held: _KindSpec(lambda origin, _reason: Held(origin), "heldではない指摘の番号です"),
    Objected: _KindSpec(
        lambda origin, reason: Objected(reason, origin), "異議の無い指摘の番号です"
    ),
}


def _replace(
    known: _Known,
    selection: Selection,
    ids: list[str],
    kind: _KindOfVerdict,
    reason: str = "",
) -> list[LostReason]:
    """番号の扱いを kind に置き換える。reason は kind が Objected のときの異議の理由。"""
    build = _KINDS[kind].build
    lost: list[LostReason] = []
    for number in ids:
        previous = selection.verdicts.get(number)
        if isinstance(previous, Objected):
            lost.append(LostReason(number, previous.reason))
        selection.verdicts[number] = build(known.origins[number], reason)
    return lost


def _remove(
    known: _Known, selection: Selection, tokens: list[str], kind: _KindOfVerdict
) -> Removal:
    """扱いが kind の番号だけを外す。

    F の番号は一つでも扱いが違えば何も外さず ReviewError にする。
    B の所属は扱いが合うものだけを外し、一件も外せなければ ReviewError にする。
    """
    mismatch = _KINDS[kind].mismatch_head
    direct, members = _split_tokens(known, tokens)
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


def pick(
    directory: Path,
    tokens: list[str],
    read_inputs: PastRoundReader,
) -> tuple[Selection, list[str], list[LostReason]]:
    """前の扱いを picked に置き換える。

    二つ目は棄却済みなのに pick した F の番号、三つ目は置き換えで消えた異議の理由。
    read_inputs は錠を握った後に呼ばれ、履歴と今の結果の実行時刻を読む。
    """
    with review_lock(directory):
        known = _locate(directory, read_inputs())
        ids = _expand(known, tokens)
        selection = load_selection(directory)
        lost = _replace(known, selection, ids, Picked)
        save_selection(directory, selection)
        # 前の回の指摘の棄却の判定は履歴の本体を要するので、この枝では今の結果の指摘だけ見る
        rejected = [i for i in ids if i in known.current and is_rejected(known.current[i].finding)]
        return selection, rejected, lost


def unpick(
    directory: Path,
    tokens: list[str],
    read_inputs: PastRoundReader,
) -> tuple[Selection, Removal]:
    with review_lock(directory):
        known = _locate(directory, read_inputs())
        selection = load_selection(directory)
        removal = _remove(known, selection, tokens, Picked)
        save_selection(directory, selection)
        return selection, removal


def hold(
    directory: Path,
    tokens: list[str],
    read_inputs: PastRoundReader,
) -> tuple[Selection, list[LostReason]]:
    with review_lock(directory):
        known = _locate(directory, read_inputs())
        ids = _expand(known, tokens)
        selection = load_selection(directory)
        lost = _replace(known, selection, ids, Held)
        save_selection(directory, selection)
        return selection, lost


def unhold(
    directory: Path,
    tokens: list[str],
    read_inputs: PastRoundReader,
) -> tuple[Selection, Removal]:
    with review_lock(directory):
        known = _locate(directory, read_inputs())
        selection = load_selection(directory)
        removal = _remove(known, selection, tokens, Held)
        save_selection(directory, selection)
        return selection, removal


def object_to(
    directory: Path,
    token: str,
    reason: str,
    read_inputs: PastRoundReader,
) -> tuple[Selection, list[LostReason]]:
    with review_lock(directory):
        if not reason.strip():
            raise ReviewError("異議の理由が空です")
        known = _locate(directory, read_inputs())
        if not _FINDING_PATTERN.match(token) or token not in known.origins:
            raise _unknown_error(known, [token])
        _require_unique(known, [token])
        selection = load_selection(directory)
        lost = _replace(known, selection, [token], Objected, reason)
        save_selection(directory, selection)
        return selection, lost


def unobject(
    directory: Path, token: str, read_inputs: PastRoundReader
) -> tuple[Selection, Removal]:
    with review_lock(directory):
        known = _locate(directory, read_inputs())
        selection = load_selection(directory)
        removal = _remove(known, selection, [token], Objected)
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
