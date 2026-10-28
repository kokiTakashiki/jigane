import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from importlib import metadata
from importlib.metadata import EntryPoint
from pathlib import Path

from jigane.languages.base import LanguageAdapter
from jigane.languages.data_places import overlap_error
from jigane.languages.plugin import (
    PLUGIN_API_VERSION,
    ApiVersion,
    Distribution,
    LanguagePlugin,
)

_MIN_OWNERS_FOR_OVERLAP = 2

ENTRY_POINT_GROUP = "jigane.languages"
ENABLED_FILE = "languages.json"


class EnabledFileError(Exception):
    """languages.json が読めない、JSON でない、または形が違う。"""


class ReasonKind(StrEnum):
    ENTRY_POINT_MISSING = "entry_point_missing"
    LOAD_ERROR = "load_error"
    VALIDATION_ERROR = "validation_error"
    # 文法の検証は次の段で行う。ここでは値の定義だけを持つ
    GRAMMAR_ERROR = "grammar_error"


@dataclass(frozen=True)
class LanguageReady:
    plugin: LanguagePlugin


@dataclass(frozen=True)
class LanguageNotEnabled:
    name: str
    distribution: str


@dataclass(frozen=True)
class LanguageUnavailable:
    """使えない言語。distribution と version は持ち主で、承認の記録から埋める。

    entry point が無い、または持ち主が一つに決まらないとき、version は None で、
    distribution は承認の記録の配布名（複数なら ", " で連ねる）になる。
    """

    name: str
    reason_kind: ReasonKind
    detail: str
    distribution: str | None = None
    version: str | None = None


LanguageState = LanguageReady | LanguageNotEnabled | LanguageUnavailable


# ESC で始まる列（CSI、OSC、それ以外の ESC 二文字）を先に除き、残りの制御文字を除く
_ESCAPE_SEQUENCE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-Z\\-_]|\x1b"
)
# 文字の並びを入れ替える双方向制御も表示を偽るので除く
# （U+061C、U+200E-200F、U+202A-202E、U+2066-2069）
_CONTROL_CHARACTER = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]"
)


def sanitize_text(text: str) -> str:
    """外のコードやレビューの対象から来た文を端末へ出せる形にする。

    改行とタブ以外の制御文字、双方向制御、端末の制御の列を除く。
    """
    return _CONTROL_CHARACTER.sub("", _ESCAPE_SEQUENCE.sub("", text))


def normalize_distribution(name: str) -> str:
    """配布名の PEP 503 正規化。大文字小文字と `-`、`_`、`.` の違いを同じ配布として扱う。"""
    return re.sub(r"[-_.]+", "-", name).lower()


class RecordError(ValueError):
    """配布の RECORD から中身を数えられない。RECORD が無い、載るファイルが無い、読めない。"""


_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class FileDigest:
    """RECORD の 1 ファイルの相対パスと、中身の sha256 の 16 進。"""

    path: str
    sha256: str


@dataclass(frozen=True)
class ApprovedContent:
    """承認した時点の配布の中身。files は path の順に並べる。"""

    files: tuple[FileDigest, ...]


@dataclass(frozen=True)
class LegacyApproval:
    """中身を記録する前の形の承認。読めるが、承認としては使わない。"""


Approval = ApprovedContent | LegacyApproval
# 承認の記録を（正規化した配布名, 版）ごとにまとめたもの。同じ組の承認は複数ありうる
ApprovalsByPair = dict[tuple[str, str], set[Approval]]


def _scan_record(dist: metadata.Distribution) -> tuple[dict[str, str], list[tuple[str, OSError]]]:
    """RECORD に載るファイルの sha256 を数える。読めなかったファイルは出会った順に返す。"""
    files = dist.files
    if files is None:
        raise RecordError("配布に RECORD が無く、中身を数えられません")
    hashes: dict[str, str] = {}
    failures: list[tuple[str, OSError]] = []
    for file in files:
        if file.name == "RECORD" and file.parent.name.endswith(".dist-info"):
            continue
        if file.parent.name == "__pycache__" and file.suffix == ".pyc":
            continue
        try:
            body = Path(str(file.locate())).read_bytes()
        except OSError as error:
            failures.append((file.as_posix(), error))
            continue
        hashes[file.as_posix()] = hashlib.sha256(body).hexdigest()
    return hashes, failures


def _unreadable_message(path: str, error: OSError) -> str:
    return f"RECORD に載る {path} を読めません: {error}"


def read_content(dist: metadata.Distribution) -> ApprovedContent:
    """配布の RECORD に載るファイルの相対パスと sha256 を、ディスク上の中身から数える。

    RECORD が持つハッシュは書き換えられるので信じない。RECORD 自身は自分のハッシュを持てないので
    除く。__pycache__ の pyc は import のたびに作られ、承認の後で変わるので除く。
    """
    hashes, failures = _scan_record(dist)
    if failures:
        path, error = failures[0]
        raise RecordError(_unreadable_message(path, error)) from error
    return ApprovedContent(
        tuple(FileDigest(path, digest) for path, digest in sorted(hashes.items()))
    )


# 理由に並べるパスの上限。RECORD は外の配布が決めるので、全部は並べない
_MAX_LISTED_PATHS = 5
LEGACY_APPROVAL_REASON = "旧い形の承認記録です（中身の照合ができません）"


@dataclass(frozen=True)
class ContentChange:
    """承認した中身と今の中身の差。パスは昇順。"""

    changed: tuple[str, ...]
    missing: tuple[str, ...]
    added: tuple[str, ...]

    def size(self) -> int:
        return len(self.changed) + len(self.missing) + len(self.added)


def _content_change(
    approved: ApprovedContent, actual: dict[str, str], absent: Iterable[str]
) -> ContentChange:
    """absent は RECORD に載るがディスクに無いファイルで、欠けたに数える。"""
    before = {file.path: file.sha256 for file in approved.files}
    return ContentChange(
        changed=tuple(sorted(p for p in before.keys() & actual.keys() if before[p] != actual[p])),
        missing=tuple(sorted((before.keys() - actual.keys()) | set(absent))),
        added=tuple(sorted(actual.keys() - before.keys())),
    )


def describe_content_change(change: ContentChange) -> str:
    """変わった、欠けた、増えたファイルのパスを、合わせて最大 5 件まで種類つきで示す。"""
    budget = _MAX_LISTED_PATHS
    parts: list[str] = []
    for label, paths in (
        ("変わった", change.changed),
        ("欠けた", change.missing),
        ("増えた", change.added),
    ):
        shown = paths[:budget]
        budget -= len(shown)
        if shown:
            parts.append(f"{label}: {', '.join(sanitize_text(p) for p in shown)}")
    if change.size() > _MAX_LISTED_PATHS:
        parts.append(f"ほか {change.size() - _MAX_LISTED_PATHS} 件")
    return f"承認のあと、配布の中身が変わっています（{'／'.join(parts)}）"


def reapproval_reason(
    approvals: Iterable[Approval], actual: dict[str, str], absent: Iterable[str] = ()
) -> str:
    """承認し直しを求める理由の文。呼ぶ側は、approvals のどれも今の中身と一致しないと確かめてある。

    中身つきの承認が複数あれば、差の最も少ないものと比べる。
    """
    recorded = [approval for approval in approvals if isinstance(approval, ApprovedContent)]
    if not recorded:
        return LEGACY_APPROVAL_REASON
    absent = tuple(absent)
    closest = min(
        (_content_change(approved, actual, absent) for approved in recorded),
        key=lambda change: (change.size(), change.changed, change.missing, change.added),
    )
    return describe_content_change(closest)


@dataclass(frozen=True)
class EnabledLanguage:
    """利用者が有効にした言語。言語の名前、提供する配布の名前、承認した配布の版、承認の中身で記録する。"""

    name: str
    distribution: str
    version: str
    content: Approval

    def __post_init__(self) -> None:
        # 正規化した形で持てば、等値と重複の除去が表記の揺れに左右されない
        object.__setattr__(self, "distribution", normalize_distribution(self.distribution))

    @classmethod
    def approve(cls, name: str, dist: metadata.Distribution) -> "EnabledLanguage":
        """配布の今の中身を承認した記録。RECORD から中身を数えられなければ RecordError。"""
        return cls(name, dist.name, dist.version, read_content(dist))

    def to_json(self) -> dict[str, object]:
        body: dict[str, object] = {
            "name": self.name,
            "distribution": self.distribution,
            "version": self.version,
        }
        if isinstance(self.content, ApprovedContent):
            body["files"] = {file.path: file.sha256 for file in self.content.files}
        return body

    @classmethod
    def from_json(cls, item: object) -> "EnabledLanguage":
        """languages.json の一件から復元する。形が違えば ValueError。"""
        legacy_keys = {"name", "distribution", "version"}
        if not isinstance(item, dict) or set(item) not in (legacy_keys, legacy_keys | {"files"}):
            raise ValueError(
                "enabled の要素は name と distribution と version と files だけを持ちます"
            )
        name, distribution, version = item["name"], item["distribution"], item["version"]
        if not all(isinstance(value, str) for value in (name, distribution, version)):
            raise ValueError("name と distribution と version は文字列でなければなりません")
        if "files" not in item:
            # 中身を記録する前の形。読めるが、承認としては使わない
            return cls(name, distribution, version, LegacyApproval())
        files = item["files"]
        if not isinstance(files, dict) or not all(
            isinstance(path, str) and isinstance(digest, str) and _SHA256_HEX.fullmatch(digest)
            for path, digest in files.items()
        ):
            raise ValueError("files は相対パスから sha256 の 16 進への対応でなければなりません")
        return cls(
            name,
            distribution,
            version,
            ApprovedContent(
                tuple(FileDigest(path, digest) for path, digest in sorted(files.items()))
            ),
        )


def load_enabled(path: Path) -> tuple[EnabledLanguage, ...]:
    if not path.exists():
        # 一度も有効にしていない利用者の通常の状態であり、誤りではない
        return ()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise EnabledFileError(f"{path} を読めません: {error}") from error
    except json.JSONDecodeError as error:
        raise EnabledFileError(f"{path} は JSON ではありません: {error}") from error
    shape_error = EnabledFileError(
        f"{path} の形が違います。"
        f'{{"enabled": [{{"name": ..., "distribution": ..., "version": ..., "files": {{...}}}}]}} '
        "だけを書きます"
    )
    if (
        not isinstance(data, dict)
        or set(data) != {"enabled"}
        or not isinstance(data["enabled"], list)
    ):
        raise shape_error
    entries: dict[EnabledLanguage, None] = {}
    for item in data["enabled"]:
        try:
            entry = EnabledLanguage.from_json(item)
        except ValueError as error:
            raise shape_error from error
        # 同じ組が重なっても誤りにしない。順序を保って一つにまとめる
        entries[entry] = None
    return tuple(entries)


def save_enabled(path: Path, enabled: Iterable[EnabledLanguage]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [e.to_json() for e in dict.fromkeys(enabled)]
    body = json.dumps({"enabled": entries}, ensure_ascii=False, indent=2)
    path.write_text(body + "\n", encoding="utf-8")


def api_version_error(version: ApiVersion) -> str | None:
    if version.major != PLUGIN_API_VERSION.major:
        return (
            f"契約の major が違います（プラグイン {version.major}、"
            f"jigane {PLUGIN_API_VERSION.major}）"
        )
    if version.minor > PLUGIN_API_VERSION.minor:
        return (
            f"プラグインの契約の minor（{version.minor}）が jigane の"
            f"（{PLUGIN_API_VERSION.minor}）より新しい"
        )
    return None


def _adapter_shape_error(adapter: LanguageAdapter) -> str | None:
    # 外のコードの値は型注釈を信用できない。集合の演算や比較に使う前に型を確かめる
    if not isinstance(adapter.name, str):
        return "アダプタの name が文字列ではありません"
    if not isinstance(adapter.grammar, str):
        return "アダプタの grammar が文字列ではありません"
    suffixes: object = adapter.suffixes
    if not isinstance(suffixes, frozenset):
        return "アダプタの suffixes が frozenset ではありません"
    if not all(isinstance(s, str) and s.startswith(".") for s in suffixes):
        return "アダプタの suffixes の要素は . で始まる文字列でなければなりません"
    return None


def _origin_error(entry_point: EntryPoint, plugin: LanguagePlugin) -> str | None:
    """出どころの誤りの説明。entry point の配布と plugin.origin の突き合わせ。無ければ None。"""
    dist = entry_point.dist
    if dist is None:
        return "entry point の配布を特定できません"
    if plugin.origin != Distribution(name=dist.name, version=dist.version):
        return f"出どころが配布 {dist.name} {dist.version} と一致しません: {plugin.origin}"
    return None


def _adapter_error(entry_point: EntryPoint, plugin: LanguagePlugin) -> str | None:
    """アダプタの誤りの説明。型、形、名前、資料の重なりの順に見る。無ければ None。"""
    if not isinstance(plugin.adapter, LanguageAdapter):
        return "アダプタが LanguageAdapter を満たしません"
    shape_error = _adapter_shape_error(plugin.adapter)
    if shape_error is not None:
        return shape_error
    if plugin.adapter.name != entry_point.name:
        return f"アダプタの名前 {plugin.adapter.name} が entry point の名前と違います"
    if plugin.data is not None:
        return overlap_error(plugin.data)
    # data が無い言語は資料を足さないので、名前の重なりは起こらない
    return None


def validate_plugin(entry_point: EntryPoint, plugin: object) -> str | None:
    """単独で判定できる誤りの説明。無ければ None。"""
    if not isinstance(plugin, LanguagePlugin):
        return f"LanguagePlugin ではなく {type(plugin).__name__} を返しました"
    origin_error = _origin_error(entry_point, plugin)
    if origin_error is not None:
        return origin_error
    version_error = api_version_error(plugin.api_version)
    if version_error is not None:
        return version_error
    return _adapter_error(entry_point, plugin)


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
    except Exception as error:  # noqa: BLE001 外のプラグインが投げる型は絞れない
        # 外のコードが何を投げるかは分からない。1 つの言語の失敗で他の言語を止めない
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
    reason = _content_error(name, candidates[0], approvals)
    if reason is not None:
        return LanguageUnavailable(
            name,
            ReasonKind.VALIDATION_ERROR,
            reason,
            _dist_name(candidates[0]),
            _dist_version(candidates[0]),
        )
    return _load(candidates[0])


def _content_error(
    name: str,
    entry_point: EntryPoint,
    approvals: ApprovalsByPair,
) -> str | None:
    """配布の今の中身が承認時と違う理由。import の前に呼ぶ。同じなら None。"""
    dist = entry_point.dist
    if dist is None:
        # 呼ぶ側が dist のある entry point だけを渡す
        return "entry point の配布を特定できません"
    again = f"jigane languages enable {name} で承認し直してください"
    approvals_here = approvals[(normalize_distribution(dist.name), dist.version)]
    if not any(isinstance(approval, ApprovedContent) for approval in approvals_here):
        # 旧い形の記録は配布を読まずに断る。RECORD の有無や読めないファイルで理由を変えない
        return f"{LEGACY_APPROVAL_REASON}。{again}"
    try:
        hashes, failures = _scan_record(dist)
    except RecordError as no_record:
        # RECORD の無い配布は、差を示す元の一覧が無い
        return f"配布の中身が承認時と違います（{no_record}）。{again}"
    unreadable = [
        (path, error) for path, error in failures if not isinstance(error, FileNotFoundError)
    ]
    if unreadable:
        # ディスクにあるのに読めないファイルは、変わった、欠けた、増えたのどれにも数えない
        path, error = unreadable[0]
        return f"配布の中身が承認時と違います（{_unreadable_message(path, error)}）。{again}"
    absent = [path for path, _ in failures]
    actual = ApprovedContent(
        tuple(FileDigest(path, digest) for path, digest in sorted(hashes.items()))
    )
    if not absent and actual in approvals_here:
        return None
    return f"{reapproval_reason(approvals_here, hashes, absent)}。{again}"


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
                f"拡張子 {suffix} が外の言語 {others} と重なります",
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
