"""群の実行の途中経過の記録。止めた実行が、金額も起動したことも残さずに消えないようにする。

実行中の記録（`run_in_progress.json`）は、実行の始めに書き、普通に終われば消す。
割り込みで止まったときは、止めた実行の記録（`stopped_runs.json`）へ移す。
どちらも `runs.json` とは別のファイルで、前の回の結果を上書きしない。
"""

import copy
import json
import os
import signal
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, assert_never

from jigane.findings.records import IN_PROGRESS_FILE
from jigane.groups.definitions import GroupId
from jigane.llm_runs import claude
from jigane.llm_runs.cost import total_by_source
from jigane.reviews.lifecycle import Review
from jigane.storage.store import review_lock, write_text_atomic
from jigane.storage.store_format import upgrade_mapping

STOPPED_FILE = "stopped_runs.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class StopKind(StrEnum):
    """実行が止まったわけ。保存物と `--json` の `kind` にこの値が入る。"""

    INTERRUPT = "interrupt"  # Ctrl-C
    TERMINATE = "terminate"  # SIGTERM
    VANISHED = "vanished"  # 強制終了などで書けず、後からプロセスの不在で見つけた

    @property
    def label(self) -> str:
        return _KIND_LABELS[self]


_KIND_LABELS = {
    StopKind.INTERRUPT: "割り込み",
    StopKind.TERMINATE: "SIGTERM",
    StopKind.VANISHED: "強制終了",
}


@dataclass(frozen=True)
class RunStart:
    """起動の時の設定。実行中の記録と止めた実行の記録が共有する。

    保存物ではこの欄を平らに出し入れする。出し入れはこの型の `to_json` / `from_json` に閉じる。
    """

    started_at: str
    model: str
    effort: str | None
    max_budget_usd: float
    pid: int

    def to_json(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "model": self.model,
            "effort": self.effort,
            "max_budget_usd": self.max_budget_usd,
            "pid": self.pid,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RunStart":
        return cls(
            started_at=data["started_at"],
            model=data["model"],
            effort=data["effort"],
            max_budget_usd=data["max_budget_usd"],
            pid=data["pid"],
        )


@dataclass(frozen=True)
class RunInProgress:
    group: GroupId
    start: RunStart

    def to_json(self) -> dict[str, Any]:
        return {"group": self.group, **self.start.to_json()}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RunInProgress":
        return cls(group=GroupId(data["group"]), start=RunStart.from_json(data))


@dataclass(frozen=True)
class StoppedRun:
    """止まった実行。金額は止めたときに分からないので、欄を持たず `cost_is_unknown` で表す。

    止めた時刻は、強制終了（VANISHED）のときだけ分からない。それ以外では必ず持つ。
    """

    kind: StopKind
    start: RunStart
    stopped_at: str | None
    completed_invocation_costs: tuple[float, ...]

    def __post_init__(self) -> None:
        if (self.kind is StopKind.VANISHED) != (self.stopped_at is None):
            raise ValueError(
                f"stopped_at is None exactly when vanished: {self.kind}, {self.stopped_at}"
            )

    @classmethod
    def stopped(cls, kind: StopKind, started: RunInProgress, costs: list[float]) -> "StoppedRun":
        return cls(kind, started.start, _now(), tuple(costs))

    @classmethod
    def vanished(cls, started: RunInProgress) -> "StoppedRun":
        return cls(StopKind.VANISHED, started.start, None, ())

    @property
    def cost_is_unknown(self) -> bool:
        return True

    @property
    def completed_cost_usd(self) -> float | None:
        if not self.completed_invocation_costs:
            return None
        return round(sum(self.completed_invocation_costs), 6)

    def to_stored_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            **self.start.to_json(),
            "stopped_at": self.stopped_at,
            "completed_invocation_costs": list(self.completed_invocation_costs),
        }

    def to_json(self) -> dict[str, Any]:
        """出力（--json）の形。和は金額の列から導けるので保存せず、出力にだけ足す。"""
        return {**self.to_stored_json(), "completed_cost_usd": self.completed_cost_usd}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "StoppedRun":
        return cls(
            StopKind(data["kind"]),
            RunStart.from_json(data),
            data["stopped_at"],
            tuple(data["completed_invocation_costs"]),
        )


class ProcessPresence(StrEnum):
    """番号のプロセスが存在するか。"""

    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"  # 確かめられない環境（Windows）


PRESENCE_UNKNOWN_NOTE: Final = (
    "Windows ではプロセスの有無を確かめられないので、実行中か止まったかを示せません"
)


class LivePresence(StrEnum):
    """記録の持ち主のプロセスについて、実行中の記録として残る二つの確かさ。

    ABSENT は含めない。ABSENT の記録は止まった実行へ移るので、実行中の記録には残らない。
    """

    PRESENT = ProcessPresence.PRESENT.value
    UNKNOWN = ProcessPresence.UNKNOWN.value  # 確かめられない環境（Windows）。記録は残す


@dataclass(frozen=True)
class LiveRun:
    """実行中の記録と、そのプロセスの確かさの組。"""

    record: RunInProgress
    presence: LivePresence

    @property
    def note(self) -> str | None:
        """確かさについて利用者へ添える一行。生きていると確かめられたときは無い。"""
        match self.presence:
            case LivePresence.UNKNOWN:
                return PRESENCE_UNKNOWN_NOTE
            case LivePresence.PRESENT:
                return None
            case _ as unreachable:
                assert_never(unreachable)

    def to_json(self) -> dict[str, Any]:
        body = self.record.to_json()
        body["presence"] = self.presence.value
        note = self.note
        if note is not None:
            body["note"] = note
        return body


@dataclass(frozen=True)
class StoppedView:
    stopped_runs: tuple[StoppedRun, ...]
    # None は実行中の記録が無いことだけを表す
    in_progress: LiveRun | None

    def to_json(self) -> dict[str, Any]:
        return {
            "stopped_runs": [run.to_json() for run in self.stopped_runs],
            "in_progress": None if self.in_progress is None else self.in_progress.to_json(),
        }


def process_presence(pid: int) -> ProcessPresence:
    """番号のプロセスが存在するか。確かめられない環境（Windows）は UNKNOWN。"""
    if os.name == "nt":
        return ProcessPresence.UNKNOWN
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return ProcessPresence.ABSENT
    except PermissionError:
        # 他の利用者のプロセスは信号を送れないが、存在はしている
        return ProcessPresence.PRESENT
    return ProcessPresence.PRESENT


# run_in_progress.json と stopped_runs.json の形の版。保存物の形を変えたら一つ上げ、
# それぞれの _UPGRADES に前の版からの移しを足す
IN_PROGRESS_FORMAT = 2
STOPPED_FORMAT = 3


def _drop_cost_usd(record: dict[str, Any]) -> None:
    # 実行の金額を記録の行に持っていた時代の鍵。今の版は金額を持たず、読み口も無い
    record.pop("cost_usd", None)


def _upgrade_in_progress_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 1 の run_in_progress.json を次の版へ移す。"""
    upgraded = copy.deepcopy(dict(raw))
    for record in upgraded.values():
        _drop_cost_usd(record)
    return upgraded


def _upgrade_stopped_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 1 の stopped_runs.json を次の版へ移す。

    種類の欄が無い行は、止めた時刻の有無から決める。時刻が無いのは強制終了だけだった。
    """
    upgraded = copy.deepcopy(dict(raw))
    for records in upgraded.values():
        for record in records:
            _drop_cost_usd(record)
            if "kind" not in record:
                record["kind"] = (
                    StopKind.VANISHED if record["stopped_at"] is None else StopKind.INTERRUPT
                ).value
    return upgraded


def _upgrade_stopped_v2(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 2 の stopped_runs.json を次の版へ移す。"""
    upgraded = copy.deepcopy(dict(raw))
    for records in upgraded.values():
        for record in records:
            # 和は completed_invocation_costs から数え直すので、書いてあった値は読まない
            record.pop("completed_cost_usd", None)
    return upgraded


# 保存された版から次の版への移し。それぞれの読む境が今の版まで順に回す
_IN_PROGRESS_UPGRADES: dict[int, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    1: _upgrade_in_progress_v1
}
_STOPPED_UPGRADES: dict[int, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    1: _upgrade_stopped_v1,
    2: _upgrade_stopped_v2,
}


def load_in_progress(raw: Mapping[str, Any]) -> dict[GroupId, RunInProgress]:
    """run_in_progress.json を読む境の一か所。今の版まで移してから型へ読む。"""
    stored = upgrade_mapping(
        IN_PROGRESS_FILE, raw, current=IN_PROGRESS_FORMAT, upgrades=_IN_PROGRESS_UPGRADES
    )
    return {GroupId(group): RunInProgress.from_json(record) for group, record in stored.items()}


def dump_in_progress(runs: Mapping[GroupId, RunInProgress]) -> dict[str, Any]:
    """run_in_progress.json へ書く境の一か所。最上位に今の版を刻む。移した結果は書き戻さない。"""
    return {"format": IN_PROGRESS_FORMAT, **{group: run.to_json() for group, run in runs.items()}}


def load_stopped(raw: Mapping[str, Any]) -> dict[GroupId, list[StoppedRun]]:
    """stopped_runs.json を読む境の一か所。今の版まで移してから型へ読む。"""
    stored = upgrade_mapping(STOPPED_FILE, raw, current=STOPPED_FORMAT, upgrades=_STOPPED_UPGRADES)
    return {
        GroupId(group): [StoppedRun.from_json(record) for record in records]
        for group, records in stored.items()
    }


def dump_stopped(runs: Mapping[GroupId, list[StoppedRun]]) -> dict[str, Any]:
    """stopped_runs.json へ書く境の一か所。最上位に今の版を刻む。移した結果は書き戻さない。"""
    return {
        "format": STOPPED_FORMAT,
        **{group: [run.to_stored_json() for run in records] for group, records in runs.items()},
    }


def _read_in_progress(directory: Path) -> dict[GroupId, RunInProgress]:
    path = directory / IN_PROGRESS_FILE
    if not path.is_file():
        return {}
    return load_in_progress(json.loads(path.read_text(encoding="utf-8")))


def _write_in_progress(directory: Path, runs: Mapping[GroupId, RunInProgress]) -> None:
    write_text_atomic(
        directory / IN_PROGRESS_FILE,
        json.dumps(dump_in_progress(runs), ensure_ascii=False, indent=2),
    )


def _read_stopped(directory: Path) -> dict[GroupId, list[StoppedRun]]:
    path = directory / STOPPED_FILE
    if not path.is_file():
        return {}
    return load_stopped(json.loads(path.read_text(encoding="utf-8")))


def _write_stopped(directory: Path, runs: Mapping[GroupId, list[StoppedRun]]) -> None:
    write_text_atomic(
        directory / STOPPED_FILE, json.dumps(dump_stopped(runs), ensure_ascii=False, indent=2)
    )


def _settle_dead(directory: Path, group: GroupId) -> None:
    """プロセスの無い実行中の記録を、強制終了で止まった実行にする。ロックの中で呼ぶ。"""
    in_progress = _read_in_progress(directory)
    started = in_progress.get(group)
    if started is None:
        return
    match process_presence(started.start.pid):
        case ProcessPresence.ABSENT:
            pass  # 続きで強制終了の記録へ移す
        case ProcessPresence.PRESENT | ProcessPresence.UNKNOWN:
            return
        case _ as unreachable:
            assert_never(unreachable)
    stopped = _read_stopped(directory)
    stopped.setdefault(group, []).append(StoppedRun.vanished(started))
    _write_stopped(directory, stopped)
    del in_progress[group]
    _write_in_progress(directory, in_progress)


def begin(review: Review, group: GroupId, settings: claude.RunSettings) -> None:
    directory = review.id.directory()
    with review_lock(directory):
        # 前の起動が強制終了で残した記録を、上書きで失わない
        _settle_dead(directory, group)
        in_progress = _read_in_progress(directory)
        start = RunStart(
            started_at=_now(),
            model=settings.budget.model,
            effort=settings.effort,
            max_budget_usd=settings.budget.max_budget_usd,
            pid=os.getpid(),
        )
        in_progress[group] = RunInProgress(group, start)
        _write_in_progress(directory, in_progress)


def finish(review: Review, group: GroupId) -> None:
    directory = review.id.directory()
    with review_lock(directory):
        in_progress = _read_in_progress(directory)
        if in_progress.pop(group, None) is not None:
            _write_in_progress(directory, in_progress)


def interrupt(review: Review, group: GroupId, costs: list[float], kind: StopKind) -> None:
    directory = review.id.directory()
    with review_lock(directory):
        in_progress = _read_in_progress(directory)
        started = in_progress.pop(group, None)
        if started is None:
            # 実行中の記録が他の起動に消されていた。移す元が無いので何も足さない
            return
        stopped = _read_stopped(directory)
        stopped.setdefault(group, []).append(StoppedRun.stopped(kind, started, costs))
        _write_stopped(directory, stopped)
        _write_in_progress(directory, in_progress)


class _Terminated(KeyboardInterrupt):
    """SIGTERM を割り込みと同じ経路に乗せるための例外。"""


def _raise_terminated(signum: int, frame: object) -> None:
    del signum, frame
    raise _Terminated


@contextmanager
def tracking(
    review: Review, group: GroupId, settings: claude.RunSettings
) -> Iterator[claude.RunSettings]:
    """実行の間、実行中の記録を持つ。終われば消し、割り込みなら止めた実行へ移す。

    返す設定の invoke は、終わった起動の金額を覚える。
    """
    costs: list[float] = []

    def recording(invocation: claude.Invocation) -> claude.InvocationResult:
        result = settings.invoke(invocation)
        costs.append(total_by_source([result.record.cost]).usd)
        return result

    begin(review, group, settings)
    previous = None
    # シグナルの登録はメインスレッドしかできない
    if threading.current_thread() is threading.main_thread():
        previous = signal.signal(signal.SIGTERM, _raise_terminated)
    try:
        yield replace(settings, invoke=recording)
    except KeyboardInterrupt as stop:
        kind = StopKind.TERMINATE if isinstance(stop, _Terminated) else StopKind.INTERRUPT
        interrupt(review, group, costs, kind)
        raise
    except BaseException:
        finish(review, group)
        raise
    else:
        finish(review, group)
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


def stopped_view(directory: Path, group: GroupId) -> StoppedView:
    """その群の止めた実行と、生きている実行中の記録。

    プロセスが生きていない実行中の記録は、強制終了で止まった実行として並べる。
    生きている記録は in_progress に返す。Windows のように判定できないときは、記録を
    止めた実行には並べず、UNKNOWN の確かさで in_progress に残す。
    """
    stopped = list(_read_stopped(directory).get(group, []))
    started = _read_in_progress(directory).get(group)
    in_progress = None
    if started is not None:
        match process_presence(started.start.pid):
            case ProcessPresence.ABSENT:
                stopped.append(StoppedRun.vanished(started))
            case ProcessPresence.PRESENT:
                in_progress = LiveRun(started, LivePresence.PRESENT)
            case ProcessPresence.UNKNOWN:
                # 確かめられないので、止めた実行には移さず記録を残す
                in_progress = LiveRun(started, LivePresence.UNKNOWN)
            case _ as unreachable:
                assert_never(unreachable)
    return StoppedView(tuple(stopped), in_progress)
