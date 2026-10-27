"""設計書のレビューを作り、refresh する。設計は docs/design-review/design-review.md の 4.2 節。

出所に保存するのは、設計書のパスと版、いまのコードの版、利用者の選択の三つだけ。
推しは保存せず、作るときと refresh のたびに設計書といまのコードから作り直す。
"""

import hashlib
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from jigane.design.parse import parse_design
from jigane.design.recommend import recommend
from jigane.design.section_map import build_section_map
from jigane.git.repository import identify
from jigane.judgment_map.declaration import Declaration, intent_declaration
from jigane.reviews.attachments import AttachmentContent, DriftedRole, VersionDrift
from jigane.reviews.lifecycle import (
    AttachedDocument,
    DesignDocContent,
    DesignDocOrigin,
    RefreshResult,
    Review,
    ReviewError,
    _new_review_id,
    _now,
    _resolve_commit,
    _staging_directory,
    judge_files,
    refresh_from_content,
    save_review,
)
from jigane.storage.store import ReviewKind

_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def default_design_doc_name(document_path: str) -> str:
    """名前を省いたときの既定。設計書のファイル名（拡張子なし）から作る。"""
    candidate = _NAME_UNSAFE.sub("-", Path(document_path).stem).strip("-._")
    return candidate or "design"


def document_version(text: bytes) -> str:
    # 版は元のバイト列から取る。正規化後から取ると、CRLF の文書の保存済みの版が全部ずれる
    return hashlib.sha256(text).hexdigest()


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class ReadDocument:
    """読んだ文書。持つのは元のバイト列だけで、版と本文はそこから導く。"""

    data: bytes

    @property
    def version(self) -> str:
        return document_version(self.data)

    @cached_property
    def text(self) -> str:
        # 本文を使わない呼び出し（版だけを取る）で解読が走らないよう、使うときまで解読しない
        return normalize_newlines(self.data.decode("utf-8"))


def read_document(path: Path) -> ReadDocument:
    """設計書と付属の文書を読む唯一の口。版は元のバイト列、本文は LF にそろえた文字列。"""
    return ReadDocument(path.read_bytes())


def _relative_path(root: Path, base: Path, path: str, label: str) -> str:
    """base からの相対で渡されたパスを、リポジトリの根からの posix の相対パスにする。"""
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ReviewError(f"{label}がリポジトリの外にあります: {path}")
    return resolved.relative_to(root.resolve()).as_posix()


def attachment_candidates(root: Path, document_path: str) -> list[str]:
    """設計書と同じ場所の reports/ の .md。付属の文書の候補で、選ぶのは利用者（--with）。

    `document_path` は根からの相対パスでも絶対パスでもよい（相対は `root` を基準に解く）。
    解いた先が `root` の外なら空を返す。返す候補は `root` からの相対パス。
    """
    document = (root / document_path).resolve()
    if not document.is_relative_to(root.resolve()):
        # リポジトリの外の設計書には、根からの相対で示せる候補が無い。落とさず空で続ける
        return []
    reports = document.parent / "reports"
    if not reports.is_dir():
        return []
    return sorted(
        path.relative_to(root.resolve()).as_posix()
        for path in reports.glob("*.md")
        if path.is_file()
    )


def _attached(root: Path, paths: tuple[str, ...]) -> tuple[AttachedDocument, ...]:
    for path in paths:
        if not (root / path).is_file():
            raise ReviewError(f"付属の文書がありません: {path}")
    return tuple(AttachedDocument(path, read_document(root / path).version) for path in paths)


def read_attachments(review: Review) -> tuple[AttachmentContent, ...]:
    """付属の文書をいまの作業ディレクトリで読み、保存した版と照らす。読み込みの唯一の置き場。"""
    origin = review.origin
    if not isinstance(origin, DesignDocOrigin):
        # 付属の文書を持つのは設計書の出所だけ。型を絞るための分岐
        raise TypeError("付属の文書は設計書の出所にだけあります")
    root = Path(review.repository_root)
    contents: list[AttachmentContent] = []
    for item in origin.attachments:
        if not (root / item.path).is_file():
            raise ReviewError(f"付属の文書がありません: {item.path}")
        read = read_document(root / item.path)
        contents.append(AttachmentContent(item.path, read.text, read.version != item.version))
    return tuple(contents)


def version_drifts(
    review: Review, document: ReadDocument, attachments: tuple[AttachmentContent, ...]
) -> tuple[VersionDrift, ...]:
    """設計書と付属の文書の、保存した版との食い違い。方針とシャードの両方の経路が引く唯一の置き場。"""
    origin = review.origin
    if not isinstance(origin, DesignDocOrigin):
        # 版を保存するのは設計書の出所だけ。型を絞るための分岐
        raise TypeError("版の照合は設計書の出所にだけあります")
    review_id = str(review.id)
    drifts: list[VersionDrift] = []
    if document.version != origin.document_version:
        drifts.append(VersionDrift(review_id, DriftedRole.MAIN, origin.document_path))
    drifts.extend(
        VersionDrift(review_id, DriftedRole.ATTACHMENT, item.path)
        for item in attachments
        if item.stale
    )
    return tuple(drifts)


def _recommended_paths(root: Path, document_text: str) -> list[str]:
    return [
        candidate.path for candidate in recommend(root, parse_design(document_text)).candidates
    ]


def _selection_warnings(selection: tuple[str, ...], recommended: list[str]) -> list[str]:
    return [
        f"選んだファイルが新しい推しに無い: {path}"
        for path in selection
        if path not in recommended
    ]


def _build_content(
    root: Path,
    document_path: str,
    selection: tuple[str, ...],
    declaration: Declaration,
    attachment_paths: tuple[str, ...] = (),
) -> DesignDocContent:
    document = read_document(root / document_path)
    try:
        code_version = _resolve_commit(root, "HEAD")
    except ReviewError as error:
        raise ReviewError(f"いまのコードの版（HEAD のコミット）を解けません: {root}") from error
    recommended = _recommended_paths(root, document.text)
    warnings = _selection_warnings(selection, recommended)
    # 選んだ後に消えたファイルは、選択を変えずに警告で示し、地図には入れない
    existing = [path for path in selection if (root / path).is_file()]
    warnings += [
        f"選んだファイルがいまのコードにありません: {path}"
        for path in selection
        if path not in existing
    ]
    judgment_map = judge_files(root, existing)
    origin = DesignDocOrigin(
        document_path,
        document.version,
        code_version,
        selection,
        _attached(root, attachment_paths),
    )
    sections = build_section_map(document_path, document.text)
    return DesignDocContent(
        tuple(existing),
        declaration,
        tuple(warnings),
        judgment_map,
        origin=origin,
        section_map=sections,
    )


def _declaration(document_path: str) -> Declaration:
    return intent_declaration(f"設計書 {document_path} のレビュー。実装の前に方針の綻びを問う")


def create_design_doc_review(
    working_directory: Path,
    document: str,
    name: str | None = None,
    select: list[str] | None = None,
    attach: list[str] | None = None,
) -> Review:
    """select が None なら、推しの候補すべてを選択にする。

    attach は付属の文書で、
    省けば付けない。
    """
    repository = identify(working_directory)
    root = repository.root
    document_path = _relative_path(root, working_directory, document, "設計書")
    if not (root / document_path).is_file():
        raise ReviewError(f"設計書がありません: {document}")
    if select is None:
        text = read_document(root / document_path).text
        selection = tuple(_recommended_paths(root, text))
    else:
        selection = tuple(
            dict.fromkeys(
                _relative_path(root, working_directory, path, "選んだファイル") for path in select
            )
        )
        for path in selection:
            if not (root / path).is_file():
                raise ReviewError(f"選んだファイルがありません: {path}")
    attachment_paths = tuple(
        dict.fromkeys(
            _relative_path(root, working_directory, path, "付属の文書") for path in attach or []
        )
    )
    resolved_name = name if name is not None else default_design_doc_name(document_path)
    review_id = _new_review_id(
        repository.owner, repository.repo, resolved_name, ReviewKind.DESIGN_DOC
    )
    content = _build_content(
        root, document_path, selection, _declaration(document_path), attachment_paths
    )
    with _staging_directory(review_id) as staging:
        review, _ = save_review(review_id, content, _now(), None, str(root), staging=staging)
    return review


def refresh_design_doc(
    review: Review, declaration_override: Declaration | None = None
) -> RefreshResult:
    """設計書の版とコードの版を取り直し、推しを作り直して警告を付ける。選択は変えない。"""
    origin = review.origin
    if not isinstance(origin, DesignDocOrigin):
        raise ReviewError(f"設計書の出所でない記録を設計書として refresh できません: {review.id}")
    root = Path(review.repository_root)
    if not (root / origin.document_path).is_file():
        raise ReviewError(f"設計書がありません: {origin.document_path}")
    return refresh_from_content(
        review,
        lambda: _build_content(
            root,
            origin.document_path,
            origin.selection,
            declaration_override or review.declaration,
            tuple(item.path for item in origin.attachments),
        ),
    )
