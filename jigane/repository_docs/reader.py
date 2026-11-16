"""設定した文書を読み、検証して、渡す文書と渡さない理由にまとめる。"""

import hashlib
import os
import stat
import sys
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from jigane.git import process, tree_files
from jigane.git.command_line import GitSubcommand, build_git_command
from jigane.git.revision import CommitSha, resolve_commit_sha
from jigane.git.tree_files import Content, NotOpened, TreeFileState, read_tree
from jigane.groups.definitions import GroupId
from jigane.llm_runs.claude import is_secret_file
from jigane.repository_docs.classify import Inside, Outside, classify_path
from jigane.repository_docs.kinds import DOCUMENT_KINDS, DocumentKind, kinds_for_groups
from jigane.repository_docs.settings import SETTINGS_FILE, load_settings
from jigane.repository_docs.skip_reason import (
    MAX_DOCUMENT_BYTES,
    HasNul,
    LinkInBase,
    Missing,
    NotAbsolute,
    NotInBase,
    NotRegularFile,
    NotUtf8,
    SecretName,
    SkipReason,
    TooLarge,
    Unreadable,
    describe,
)

_SYMLINK_MODE = "120000"


@dataclass(frozen=True)
class BaseTree:
    """展開済みの起点の木。PR と範囲のレビューが使う。"""

    directory: Path


@dataclass(frozen=True)
class HeadOfCheckout:
    """分類で見つけたチェックアウトの HEAD。起点が無いレビューと setup が使う。"""


BaseSource = BaseTree | HeadOfCheckout


@dataclass(frozen=True)
class Document:
    kind: DocumentKind
    text: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class RepositoryDocs:
    given: tuple[Document, ...]
    skipped: tuple[tuple[DocumentKind, SkipReason], ...]


NO_DOCS = RepositoryDocs((), ())


def _precheck_outside(path: Path) -> SkipReason | None:
    # Windows ではディレクトリを開くと権限拒否になるので、開く前に通常のファイルかを分類する
    try:
        return None if stat.S_ISREG(path.stat().st_mode) else NotRegularFile()
    except (FileNotFoundError, NotADirectoryError):
        return Missing()
    except OSError:
        return None  # 調べられなくても、開く側が同じ失敗を Unreadable として返す


def _read_outside(path: Path) -> bytes | SkipReason:
    refusal = _precheck_outside(path)
    return refusal if refusal is not None else _open_and_read(path)


def _open_and_read(path: Path) -> bytes | SkipReason:
    # FIFO を開いて止まらないよう O_NONBLOCK で開く。stat と open の間の差し替えは fstat で拾う
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, NotADirectoryError):
        return Missing()
    except OSError as error:
        return Unreadable(error.strerror or type(error).__name__)
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            return NotRegularFile()
        if status.st_size > MAX_DOCUMENT_BYTES:
            return TooLarge(status.st_size)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(MAX_DOCUMENT_BYTES + 1)
    except OSError as error:
        return Unreadable(error.strerror or type(error).__name__)
    finally:
        os.close(descriptor)


def _read_base_tree(root: Path, relative: str) -> bytes | SkipReason:
    match read_tree(root, relative, MAX_DOCUMENT_BYTES):
        case Content(data=data):
            return data
        case tree_files.TooLarge():
            # 実際の大きさは読まずに止めたので分からない。上限を一つ超えた値で超過を表す
            return TooLarge(MAX_DOCUMENT_BYTES + 1)
        case NotOpened(state=TreeFileState.REFUSED):
            return LinkInBase()
        case NotOpened(state=TreeFileState.MISSING):
            return NotInBase()
        case NotOpened(state=TreeFileState.NOT_REGULAR):
            return NotRegularFile()
        case NotOpened(detail=detail):
            # 残りは UNREADABLE。READABLE の NotOpened は作られない
            return Unreadable(detail)


def _head_entry(checkout_root: Path, head: CommitSha, relative: str) -> SkipReason | None:
    """HEAD の木の中の項目が読めるものなら None、読めないなら理由を返す。"""
    try:
        listed = process.run_command(
            build_git_command(
                GitSubcommand.LS_TREE,
                global_options=["--literal-pathspecs"],
                options=["-z", "-l"],
                revisions=[head],
                pathspecs=[relative],
            ),
            cwd=checkout_root,
        )
    except process.CommandError as error:
        return Unreadable(error.stderr.strip()[:200] or "git ls-tree に失敗しました")
    meta = listed.split("\0")[0].partition("\t")[0]
    fields = meta.split()
    if not fields:
        return NotInBase()
    mode, object_type, _, size = fields
    if mode == _SYMLINK_MODE:
        return LinkInBase()
    if object_type != "blob":
        return NotRegularFile()
    return TooLarge(int(size)) if int(size) > MAX_DOCUMENT_BYTES else None


def _read_head(checkout_root: Path, relative: str) -> bytes | SkipReason:
    try:
        head = resolve_commit_sha(checkout_root, "HEAD")
    except process.CommandError:
        # コミットの無いリポジトリでは HEAD に何も無いので、起点に無い扱いにする
        return NotInBase()
    refusal = _head_entry(checkout_root, head, relative)
    if refusal is not None:
        return refusal
    try:
        return process.run_command_bytes(
            build_git_command(GitSubcommand.SHOW, revision_paths=[(head, relative)]),
            cwd=checkout_root,
        )
    except process.CommandError as error:
        return Unreadable(error.stderr.strip()[:200] or "git show に失敗しました")


def _read_bytes(path: str, owner: str, repo: str, base: BaseSource) -> bytes | SkipReason:
    if not Path(path).is_absolute():
        return NotAbsolute()
    where = classify_path(path, owner, repo)
    resolved_name = where.relative if isinstance(where, Inside) else where.resolved.name
    # リンクの先の名前でも拒む。CLAUDE.md を .env へのリンクにして送らせないため
    if is_secret_file(path) or is_secret_file(resolved_name):
        return SecretName()
    if isinstance(where, Outside):
        return _read_outside(where.resolved)
    match base:
        case BaseTree(directory=directory):
            return _read_base_tree(directory, where.relative)
        case HeadOfCheckout():
            return _read_head(where.checkout_root, where.relative)


def read_document(
    kind: DocumentKind, path: str, owner: str, repo: str, base: BaseSource
) -> Document | SkipReason:
    """一つの文書を読み、次の順に検証する。上限は上限 + 1 バイトまでしか読まずに判定する。

    絶対パスか、秘密のファイル名か、無いか、通常のファイルか、
    上限を超えないか、UTF-8 か、NUL を含まないか。
    """
    data = _read_bytes(path, owner, repo, base)
    if not isinstance(data, bytes):
        return data
    if len(data) > MAX_DOCUMENT_BYTES:
        return TooLarge(len(data))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return NotUtf8()
    if "\0" in text:
        return HasNul()
    return Document(kind, text, len(data), hashlib.sha256(data).hexdigest())


def read_repository_docs(
    groups: Collection[GroupId], owner: str, repo: str, base: BaseSource
) -> RepositoryDocs:
    """群が必要とする種類の文書を読む。文書の不備は種類ごとに一度、標準エラーに警告して続ける。

    設定ファイルの誤りは RepositoryDocsError を送出する。
    """
    wanted = kinds_for_groups(groups)
    configured = load_settings().resolve(owner, repo)
    given: list[Document] = []
    skipped: list[tuple[DocumentKind, SkipReason]] = []
    for spec in DOCUMENT_KINDS:
        if spec.kind not in wanted or spec.kind not in configured:
            continue
        entry = configured[spec.kind]
        outcome = read_document(spec.kind, entry.value, owner, repo, base)
        if isinstance(outcome, Document):
            given.append(outcome)
            continue
        skipped.append((spec.kind, outcome))
        sys.stderr.write(
            f"警告: {spec.heading}を渡さずに続けます。{SETTINGS_FILE} の {entry.origin}: "
            f"{describe(outcome)}\n"
        )
    return RepositoryDocs(tuple(given), tuple(skipped))
