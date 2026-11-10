from collections.abc import Iterable, Sequence
from dataclasses import replace
from importlib.metadata import EntryPoint

from jigane.languages.approval import ApprovalsByPair, content_error
from jigane.languages.enabled import EnabledLanguage
from jigane.languages.plugin import LanguagePlugin
from jigane.languages.states import (
    LanguageNotEnabled,
    LanguageReady,
    LanguageState,
    LanguageUnavailable,
    ReasonKind,
)
from jigane.languages.validation import validate_plugin
from jigane.text.distribution_names import normalize_distribution

_MIN_OWNERS_FOR_OVERLAP = 2

ENTRY_POINT_GROUP = "jigane.languages"


def _dist_name(entry_point: EntryPoint) -> str:
    dist = entry_point.dist
    return dist.name if dist is not None else "(不明)"


def _dist_version(entry_point: EntryPoint) -> str:
    dist = entry_point.dist
    return dist.version if dist is not None else "(不明)"


def _owner(
    approved: set[tuple[str, str]], entry_points: list[EntryPoint]
) -> tuple[str, str | None]:
    """使えない言語の持ち主。承認の配布の entry point が一つならその配布と版、他は承認の名前。"""
    approved_distributions = {distribution for distribution, _ in approved}
    matching = [
        c.dist
        for c in entry_points
        if c.dist is not None and normalize_distribution(c.dist.name) in approved_distributions
    ]
    if len(matching) == 1:
        return matching[0].name, matching[0].version
    return ", ".join(sorted(approved_distributions)), None


def _load(entry_point: EntryPoint) -> LanguagePlugin | LanguageUnavailable:
    try:
        factory = entry_point.load()
        plugin: object = factory()
    except Exception as error:  # noqa: BLE001 プラグインが投げる型は絞れない
        # プラグインのコードが何を投げるかは分からない。1 つの言語の失敗で他の言語を止めない
        return LanguageUnavailable(
            entry_point.name,
            ReasonKind.LOAD_ERROR,
            f"{type(error).__name__}: {error}",
            _dist_name(entry_point),
            entry_point.dist.version if entry_point.dist is not None else None,
        )
    reason = validate_plugin(entry_point, plugin)
    if reason is not None or not isinstance(plugin, LanguagePlugin):
        # 型が違う場合の説明は validate_plugin が返しているので、reason は None にならない
        return LanguageUnavailable(entry_point.name, ReasonKind.VALIDATION_ERROR, reason or "")
    return plugin


def _classify(
    name: str,
    entry_points: list[EntryPoint],
    approved_pairs: set[tuple[str, str]],
    approvals: ApprovalsByPair,
    builtin_names: set[str],
) -> LanguagePlugin | LanguageUnavailable:
    """承認された名前一つ分の結果。load できた plugin か、使えない理由。"""
    approved_distributions = {distribution for distribution, _ in approved_pairs}
    same_distribution = [
        c
        for c in entry_points
        if c.dist is not None and normalize_distribution(c.dist.name) in approved_distributions
    ]
    # 名前と配布に加えて、承認した版と同じ版の entry point だけを load の対象にする
    candidates = [
        c
        for c in same_distribution
        if c.dist is not None
        and (normalize_distribution(c.dist.name), c.dist.version) in approved_pairs
    ]
    if same_distribution and not candidates:
        installed = ", ".join(
            sorted(f"{_dist_name(c)} {_dist_version(c)}" for c in same_distribution)
        )
        return LanguageUnavailable(
            name,
            ReasonKind.VALIDATION_ERROR,
            f"承認した版と違う版が入っています（{installed}）。"
            f"jigane languages enable {name} で承認し直してください",
        )
    if name in builtin_names:
        return LanguageUnavailable(
            name, ReasonKind.VALIDATION_ERROR, "組み込みの言語と同じ名前です"
        )
    if not candidates:
        dists = ", ".join(sorted(_dist_name(c) for c in entry_points))
        return LanguageUnavailable(
            name,
            ReasonKind.VALIDATION_ERROR,
            f"有効にした配布と違う配布の entry point です: {dists}",
        )
    if len(candidates) > 1:
        dists = ", ".join(sorted(_dist_name(c) for c in candidates))
        return LanguageUnavailable(
            name,
            ReasonKind.VALIDATION_ERROR,
            f"同じ名前の entry point が複数あります: {dists}",
        )
    reason = _reapproval_guidance(name, candidates[0], approvals)
    if reason is not None:
        return LanguageUnavailable(
            name,
            ReasonKind.VALIDATION_ERROR,
            reason,
            _dist_name(candidates[0]),
            _dist_version(candidates[0]),
        )
    return _load(candidates[0])


def _reapproval_guidance(
    name: str,
    entry_point: EntryPoint,
    approvals: ApprovalsByPair,
) -> str | None:
    """配布の中身が承認時と違えば、理由に承認し直しの案内を添えた文。同じなら None。

    import の前に呼ぶ。
    """
    dist = entry_point.dist
    if dist is None:
        # 呼ぶ側が dist のある entry point だけを渡す
        return "entry point の配布を特定できません"
    reason = content_error(dist, approvals[(normalize_distribution(dist.name), dist.version)])
    if reason is None:
        return None
    return f"{reason}。jigane languages enable {name} で承認し直してください"


def _load_approved(
    by_name: dict[str, list[EntryPoint]],
    approved: dict[str, set[tuple[str, str]]],
    approvals: dict[str, ApprovalsByPair],
    builtin_names: set[str],
) -> tuple[dict[str, LanguagePlugin], dict[str, LanguageUnavailable]]:
    """承認された名前の entry point を load して、使える言語と使えない言語に分ける。"""
    loaded: dict[str, LanguagePlugin] = {}
    failures: dict[str, LanguageUnavailable] = {}
    for name in sorted(by_name):
        if name not in approved:
            # 有効でない entry point は import しない。一覧に出す分は後段で作る
            continue
        result = _classify(name, by_name[name], approved[name], approvals[name], builtin_names)
        if isinstance(result, LanguageUnavailable):
            failures[name] = result
        else:
            loaded[name] = result
    return loaded, failures


def _reject_overlaps(
    loaded: dict[str, LanguagePlugin],
    failures: dict[str, LanguageUnavailable],
    builtin_suffixes: set[str],
) -> None:
    """外どうしで拡張子が重なる言語を、loaded から外して failures へ移す。"""
    # 外どうしで拡張子が重なる言語は、どちらが勝つかを決められないので全て誤りにする。
    # 組み込みと重なる拡張子は組み込みが勝つので、ここでは数えない。
    owners: dict[str, list[str]] = {}
    for name, plugin in loaded.items():
        for suffix in plugin.adapter.suffixes - builtin_suffixes:
            owners.setdefault(suffix, []).append(name)
    for suffix, names in sorted(owners.items()):
        if len(names) < _MIN_OWNERS_FOR_OVERLAP:
            # 重なっていない拡張子は誤りにならない
            continue
        for name in names:
            others = ", ".join(sorted(n for n in names if n != name))
            loaded.pop(name, None)
            failures[name] = LanguageUnavailable(
                name,
                ReasonKind.VALIDATION_ERROR,
                f"拡張子 {suffix} が追加の言語 {others} と重なります",
            )


def _listed_states(
    by_name: dict[str, list[EntryPoint]],
    approved: dict[str, set[tuple[str, str]]],
    loaded: dict[str, LanguagePlugin],
    failures: dict[str, LanguageUnavailable],
) -> list[LanguageState]:
    """entry point と承認の突き合わせ結果を、名前順の状態の並びにする。"""

    def owned(failure: LanguageUnavailable) -> LanguageUnavailable:
        owner = _owner(approved[failure.name], by_name.get(failure.name, []))
        return replace(failure, distribution=owner[0], version=owner[1])

    states: list[LanguageState] = []
    for name in sorted(by_name):
        if name in failures:
            states.append(owned(failures[name]))
        elif name in loaded:
            states.append(LanguageReady(loaded[name]))
        else:
            states.extend(LanguageNotEnabled(name, _dist_name(c)) for c in by_name[name])
    states.extend(
        owned(
            LanguageUnavailable(
                name, ReasonKind.ENTRY_POINT_MISSING, "対応する entry point がありません"
            )
        )
        for name in approved
        if name not in by_name
    )
    return states


def discover(
    entry_points: Iterable[EntryPoint],
    enabled: Sequence[EnabledLanguage],
    builtins: Sequence[LanguagePlugin],
) -> tuple[LanguageState, ...]:
    builtin_names = {plugin.adapter.name for plugin in builtins}
    builtin_suffixes = {s for plugin in builtins for s in plugin.adapter.suffixes}
    by_name: dict[str, list[EntryPoint]] = {}
    for entry_point in entry_points:
        by_name.setdefault(entry_point.name, []).append(entry_point)
    approved: dict[str, set[tuple[str, str]]] = {}
    approvals: dict[str, ApprovalsByPair] = {}
    for item in enabled:
        approved.setdefault(item.name, set()).add((item.distribution, item.version))
        by_pair = approvals.setdefault(item.name, {})
        by_pair.setdefault((item.distribution, item.version), set()).add(item.content)

    loaded, failures = _load_approved(by_name, approved, approvals, builtin_names)
    _reject_overlaps(loaded, failures, builtin_suffixes)
    return (
        *(LanguageReady(plugin) for plugin in builtins),
        *_listed_states(by_name, approved, loaded, failures),
    )
