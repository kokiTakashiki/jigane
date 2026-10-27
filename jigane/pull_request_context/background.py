"""変更の背景の材料の決定的な収集。

PR の本文が参照する文書、
同じファイルに触れた過去の PR、変更したパスの所有者。
"""

import copy
import json
import posixpath
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, assert_never

import pathspec

from jigane.git import process
from jigane.git.tree_files import is_inside_tree, read_tree_text
from jigane.judgment_map.classification import FilePair
from jigane.pull_request_context.fetching import PullRequestMetadata
from jigane.storage.store import write_text_atomic
from jigane.storage.store_format import Upgrade, upgrade_mapping

BACKGROUND_FILE = "background.json"
# background.json の形の版。保存物の形を変えたら一つ上げ、_UPGRADES に前の版からの移しを足す
BACKGROUND_FORMAT = 3
# 過去の PR の件数の上限。プロンプトの大きさを抑える
PAST_PULL_REQUEST_LIMIT = 5
# GitHub が CODEOWNERS を探す場所。先に見つかったものを使う
CODEOWNERS_LOCATIONS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")

# プロンプトと show に出すコミットの ID の長さ
COMMIT_ABBREVIATION_LENGTH = 7

# 所有者の無いパスの表示。プロンプトと show で揃える
OWNERLESS_TEXT = "所有者なし"

_URL_PATTERN = re.compile(r"https?://[^\s<>()\[\]\"']+")
_PATH_PATTERN = re.compile(r"(?<![\w/.\-@:])(?:\./)?[\w.\-]+(?:/[\w.\-]+)*")
_BLOB_PATTERN = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$")
_MERGE_SUBJECT_PATTERN = re.compile(r"^Merge pull request #(\d+) from ")
_SQUASH_SUBJECT_PATTERN = re.compile(r"^(.*?)\s*\(#(\d+)\)$")
_RECORD_START = "\x01"
_FIELD_SEPARATOR = "\x02"
_BODY_END = "\x03"


@dataclass(frozen=True)
class PastPullRequest:
    number: int
    # マージのコミットで本文にも題が無いときは空
    title: str
    # 変更したファイルのうち、この PR が触れたもの
    files: tuple[str, ...]

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "PastPullRequest":
        return PastPullRequest(data["number"], data["title"], tuple(data["files"]))


@dataclass(frozen=True)
class PastCommit:
    """題に PR の番号を持たないコミット。main へ直に積むリポジトリの履歴を受ける。"""

    sha: str
    title: str
    # 変更したファイルのうち、このコミットが触れたもの
    files: tuple[str, ...]

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "PastCommit":
        return PastCommit(data["sha"], data["title"], tuple(data["files"]))


# 種類ごとの型の和。番号の有無を任意の項目では表さない
PastChange = PastPullRequest | PastCommit


class PastChangeKind(StrEnum):
    """保存物の過去の変更の種類の印。値は background.json がそのまま持つ。"""

    PULL_REQUEST = "pull_request"
    COMMIT = "commit"


def _past_change_to_dict(change: PastChange) -> dict[str, Any]:
    match change:
        case PastPullRequest():
            return {
                "kind": PastChangeKind.PULL_REQUEST.value,
                **asdict(change),
                "files": list(change.files),
            }
        case PastCommit():
            return {
                "kind": PastChangeKind.COMMIT.value,
                **asdict(change),
                "files": list(change.files),
            }
        case _:
            assert_never(change)


def _past_change_from_dict(data: dict[str, Any]) -> PastChange:
    kind = PastChangeKind(data["kind"])
    match kind:
        case PastChangeKind.PULL_REQUEST:
            return PastPullRequest.from_dict(data)
        case PastChangeKind.COMMIT:
            return PastCommit.from_dict(data)
        case _:
            assert_never(kind)


@dataclass(frozen=True)
class PathOwners:
    path: str
    # 空は所有者なし
    owners: tuple[str, ...]

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "PathOwners":
        return PathOwners(data["path"], tuple(data["owners"]))


@dataclass(frozen=True)
class Background:
    # 変更後のツリーに実在するファイルの、リポジトリのルートからの posix の相対パス
    documents: tuple[str, ...]
    # 新しい順
    past_changes: tuple[PastChange, ...]
    owners: tuple[PathOwners, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["documents"] = list(self.documents)
        data["owners"] = [{"path": item.path, "owners": list(item.owners)} for item in self.owners]
        data["past_changes"] = [_past_change_to_dict(change) for change in self.past_changes]
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Background":
        return Background(
            tuple(data["documents"]),
            tuple(_past_change_from_dict(item) for item in data["past_changes"]),
            tuple(PathOwners.from_dict(item) for item in data["owners"]),
        )


def _existing_file(head_tree: Path, relative: str) -> str | None:
    normalized = posixpath.normpath(relative)
    if normalized.startswith("..") or posixpath.isabs(normalized):
        return None
    # シンボリックリンクと、変更後のツリーの外を指すものは参照に入れない
    if not is_inside_tree(head_tree, normalized):
        return None
    return normalized


def _blob_document(url: str, owner: str, repo: str, head_tree: Path) -> str | None:
    match = _BLOB_PATTERN.match(url.split("#", 1)[0].split("?", 1)[0])
    if match is None:
        return None
    if (match[1].lower(), match[2].lower()) != (owner.lower(), repo.lower()):
        return None
    # ref にスラッシュを含みうるため、先頭から何区切りまでを ref とするかを試す
    segments = match[3].split("/")
    for split in range(1, len(segments)):
        found = _existing_file(head_tree, "/".join(segments[split:]))
        if found is not None:
            return found
    return None


def find_documents(body: str, owner: str, repo: str, head_tree: Path) -> list[str]:
    found: dict[str, None] = {}
    # 本文の出現順を保つため、URL と相対パスの位置を合わせて並べる
    hits: list[tuple[int, str]] = []
    for match in _URL_PATTERN.finditer(body):
        document = _blob_document(match.group().rstrip(".,;:"), owner, repo, head_tree)
        if document is not None:
            hits.append((match.start(), document))
    without_urls = _URL_PATTERN.sub(lambda m: " " * len(m.group()), body)
    for match in _PATH_PATTERN.finditer(without_urls):
        token = match.group().rstrip(".")
        if "/" not in token and "." not in token.lstrip("."):
            # 拡張子もディレクトリも無い単語は、ファイル名として偶然一致しうる
            continue
        document = _existing_file(head_tree, token.removeprefix("./"))
        if document is not None:
            hits.append((match.start(), document))
    for _, document in sorted(hits):
        found[document] = None
    return list(found)


def _pull_request_from_subject(subject: str, body: str) -> tuple[int, str] | None:
    merge = _MERGE_SUBJECT_PATTERN.match(subject)
    if merge is not None:
        # マージのコミットの題は、件名でなく本文の最初の行に入る
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        return int(merge[1]), lines[0] if lines else ""
    squash = _SQUASH_SUBJECT_PATTERN.match(subject)
    if squash is not None:
        return int(squash[2]), squash[1]
    return None


def find_past_pull_requests(
    git_dir: Path, merge_base: str, pairs: list[FilePair], own_number: int | None
) -> list[PastPullRequest]:
    paths = list(
        dict.fromkeys(path for pair in pairs for path in (pair.old_path, pair.new_path) if path)
    )
    if not paths:
        return []
    # first-parent と -m で、マージのコミットも squash のコミットも 1 本の履歴に並ぶ
    output = process.run_command(
        [
            "git",
            "-c",
            "core.quotepath=off",
            "--literal-pathspecs",
            "log",
            "--first-parent",
            "-m",
            "--no-renames",
            "--name-only",
            f"--format={_RECORD_START}%s{_FIELD_SEPARATOR}%b{_BODY_END}",
            merge_base,
            "--",
            *paths,
        ],
        cwd=git_dir,
    )
    found: dict[int, tuple[str, dict[str, None]]] = {}
    for record in output.split(_RECORD_START)[1:]:
        head, _, names = record.partition(_BODY_END)
        subject, _, body = head.partition(_FIELD_SEPARATOR)
        parsed = _pull_request_from_subject(subject, body)
        if parsed is None or parsed[0] == own_number:
            continue
        number, title = parsed
        if number not in found:
            found[number] = (title, {})
        for name in names.splitlines():
            if name:
                found[number][1][name] = None
    return [
        PastPullRequest(number, title, tuple(files))
        for number, (title, files) in list(found.items())[:PAST_PULL_REQUEST_LIMIT]
    ]


def find_past_changes(git_dir: Path, merge_base: str, pairs: list[FilePair]) -> list[PastChange]:
    """範囲の基点より前に、範囲のファイルに触れた変更。first-parent に限らない。

    題に PR の番号を持つコミットは PR、持たない通常のコミットは PastCommit にする。
    """
    paths = list(
        dict.fromkeys(path for pair in pairs for path in (pair.old_path, pair.new_path) if path)
    )
    if not paths:
        return []
    output = process.run_command(
        [
            "git",
            "-c",
            "core.quotepath=off",
            "--literal-pathspecs",
            "log",
            "-m",
            "--no-renames",
            "--name-only",
            (
                f"--format={_RECORD_START}%H{_FIELD_SEPARATOR}%P{_FIELD_SEPARATOR}%s"
                f"{_FIELD_SEPARATOR}%b{_BODY_END}"
            ),
            merge_base,
            "--",
            *paths,
        ],
        cwd=git_dir,
    )
    found: dict[tuple[PastChangeKind, str | int], tuple[str, dict[str, None]]] = {}
    for record in output.split(_RECORD_START)[1:]:
        head, _, names = record.partition(_BODY_END)
        sha, parents, subject, body = head.split(_FIELD_SEPARATOR, 3)
        parsed = _pull_request_from_subject(subject, body)
        if parsed is not None:
            key: tuple[PastChangeKind, str | int] = (PastChangeKind.PULL_REQUEST, parsed[0])
            title = parsed[1]
        elif len(parents.split()) > 1:
            # 番号の無いマージのコミットは変更の中身を持たない。側枝のコミットが別に並ぶ
            continue
        else:
            key = (PastChangeKind.COMMIT, sha)
            title = subject
        if key not in found:
            found[key] = (title, {})
        for name in names.splitlines():
            if name:
                found[key][1][name] = None
    changes: list[PastChange] = []
    for (_kind, ident), (title, files) in found.items():
        if isinstance(ident, int):
            changes.append(PastPullRequest(ident, title, tuple(files)))
        else:
            changes.append(PastCommit(ident, title, tuple(files)))
    return changes[:PAST_PULL_REQUEST_LIMIT]


def _read_codeowners(base_tree: Path) -> list[tuple[pathspec.PathSpec[Any], list[str]]]:
    for location in CODEOWNERS_LOCATIONS:
        text = read_tree_text(base_tree, location)
        if text is not None:
            break
    else:
        return []
    rules: list[tuple[pathspec.PathSpec[Any], list[str]]] = []
    for line in text.splitlines():
        tokens = line.split()
        if not tokens or tokens[0].startswith("#"):
            continue
        owners: list[str] = []
        for token in tokens[1:]:
            if token.startswith("#"):
                break
            owners.append(token)
        rules.append((pathspec.PathSpec.from_lines("gitignore", [tokens[0]]), owners))
    return rules


def find_owners(base_tree: Path, pairs: list[FilePair]) -> list[PathOwners]:
    rules = _read_codeowners(base_tree)
    result: list[PathOwners] = []
    for pair in pairs:
        # 削除されたファイルは変更後のパスが無いので、変更前のパスで引く
        path = pair.new_path or pair.old_path
        if path is None:
            continue
        owners: list[str] = []
        for spec, rule_owners in rules:
            if spec.match_file(path):
                # 後に書いた規則が優先する。所有者の無い規則は所有者を外す
                owners = rule_owners
        result.append(PathOwners(path, tuple(owners)))
    return result


def has_owners(background: Background) -> bool:
    # 規則に当たらないパスだけの一覧は、材料にならない
    return any(item.owners for item in background.owners)


def collect_background(
    metadata: PullRequestMetadata,
    head_tree: Path,
    base_tree: Path,
    git_dir: Path,
    merge_base: str,
    *,
    pairs: list[FilePair],
) -> Background:
    return Background(
        **collect_declaration_background(metadata, head_tree),
        past_changes=tuple(find_past_pull_requests(git_dir, merge_base, pairs, metadata.number)),
        owners=tuple(find_owners(base_tree, pairs)),
    )


def collect_range_background(
    working_directory: Path, base_tree: Path, merge_base: str, pairs: list[FilePair]
) -> Background:
    """手元の範囲の背景。CODEOWNERS と作業ディレクトリの履歴だけで、GitHub の材料は持たない。"""
    return Background(
        (),
        # 範囲には自身の PR の番号が無く、除外するものが無い
        tuple(find_past_changes(working_directory, merge_base, pairs)),
        tuple(find_owners(base_tree, pairs)),
    )


def collect_declaration_background(
    metadata: PullRequestMetadata, head_tree: Path
) -> dict[str, Any]:
    """PR のタイトルと本文から決まる部分。ツリーが同じ PR の更新でも集め直す。"""
    documents = find_documents(metadata.body or "", metadata.owner, metadata.repo, head_tree)
    return {"documents": tuple(documents)}


def dump_background(background: Background) -> dict[str, Any]:
    """background.json へ書く境の一か所。最上位に今の版を刻む。"""
    return {"format": BACKGROUND_FORMAT, **background.to_dict()}


def _upgrade_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 1 の background.json を次の版へ移す。"""
    upgraded = copy.deepcopy(dict(raw))
    # 版 1 のうち古いものは past_pull_requests に PR だけを持つ。種類の印が無いので PR として読む
    legacy = upgraded.pop("past_pull_requests", [])
    if "past_changes" not in upgraded:
        upgraded["past_changes"] = [
            {"kind": PastChangeKind.PULL_REQUEST.value, **item} for item in legacy
        ]
    # 所有者の一覧を持つ前の保存物は、所有者を集めていない。空の一覧と読む
    upgraded.setdefault("owners", [])
    # 読み口を持たないまま消えた鍵
    upgraded.pop("github_material", None)
    return upgraded


def _upgrade_v2(raw: Mapping[str, Any]) -> dict[str, Any]:
    """版 2 の background.json を次の版へ移す。"""
    upgraded = copy.deepcopy(dict(raw))
    # 背景から issue を外した。版 2 までの保存物が持つ issues は読み口が無いので落とす
    upgraded.pop("issues", None)
    return upgraded


# 保存された版から次の版への移し。parse_background が BACKGROUND_FORMAT まで順に回す
_UPGRADES: dict[int, Upgrade] = {1: _upgrade_v1, 2: _upgrade_v2}


def parse_background(raw: Mapping[str, Any]) -> Background:
    """background.json を読む境の一か所。保存された版から今の版まで移してから型へ読む。"""
    stored = upgrade_mapping(BACKGROUND_FILE, raw, current=BACKGROUND_FORMAT, upgrades=_UPGRADES)
    return Background.from_dict(dict(stored))


def save_background(directory: Path, background: Background) -> None:
    write_text_atomic(
        directory / BACKGROUND_FILE,
        json.dumps(dump_background(background), ensure_ascii=False, indent=2),
    )


def load_background(directory: Path) -> Background | None:
    path = directory / BACKGROUND_FILE
    if not path.is_file():
        # 機能レビュー式と、背景を集める前に作った差分レビュー式には無い
        return None
    return parse_background(json.loads(path.read_text(encoding="utf-8")))
