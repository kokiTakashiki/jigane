"""SARIF の URI を、リポジトリの根からの posix の相対パスに直す。"""

import posixpath
import re
from pathlib import Path

from jigane.git.process import is_windows
from jigane.languages.plugins import sanitize_text
from jigane.static_analysis.models import DropReason

# URI のスキームは英字で始まる。1 文字のスキームはドライブ文字の `C:` と形が同じなので、
# 先にドライブ文字として見る
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
_PERCENT_RUN = re.compile(r"(?:%[0-9A-Fa-f]{2})+")
_DRIVE = re.compile(r"[A-Za-z]:(?:[/\\]|$)")
_WINDOWS_FILE_PATH = re.compile(r"/[A-Za-z]:/")
_QUERY_OR_FRAGMENT = re.compile(r"[?#]")


def resolve_uri(uri: str, analysis_root: Path, repository_root: Path) -> str | DropReason:
    """リポジトリの根からの相対パスか、捨てる理由を返す。根は絶対パスで渡す。"""
    if _DRIVE.match(uri):
        return DropReason.DRIVE_LETTER
    scheme = _SCHEME.match(uri)
    name = scheme.group(1).lower() if scheme else ""
    if name not in ("", "file"):
        return DropReason.UNSUPPORTED_SCHEME
    if "\0" in uri:
        return DropReason.NUL_CHARACTER
    # クエリと断片は、パスの一部でも、指摘の位置でもない
    without_query = _QUERY_OR_FRAGMENT.split(uri, maxsplit=1)[0]
    if scheme is not None:
        relative = _from_file_uri(without_query[scheme.end() :], analysis_root)
    else:
        relative = _percent_decode(without_query)
    if isinstance(relative, DropReason):
        return relative
    if "\0" in relative:
        return DropReason.NUL_CHARACTER
    return _finish(relative, analysis_root, repository_root)


def _percent_decode(text: str) -> str:
    return _PERCENT_RUN.sub(
        lambda found: bytes.fromhex(found.group().replace("%", "")).decode("utf-8", "replace"),
        text,
    )


def _from_file_uri(after_scheme: str, analysis_root: Path) -> str | DropReason:
    if after_scheme.startswith("//"):
        host, slash, rest = after_scheme[2:].partition("/")
        if host != "":
            return DropReason.FILE_URI_WITH_HOST
        after_scheme = slash + rest
    decoded = _percent_decode(after_scheme).replace("\\", "/")
    if "\0" in decoded:
        return DropReason.NUL_CHARACTER
    return _relative_to_analysis_root(decoded, analysis_root)


def _finish(relative: str, analysis_root: Path, repository_root: Path) -> str | DropReason:
    normalized = posixpath.normpath(relative.replace("\\", "/"))
    reason = _classify_unusable(normalized)
    if reason is not None:
        return reason
    # sanitize_text は改行とタブを残すが、パスには置かせない
    if sanitize_text(normalized) != normalized or "\n" in normalized or "\t" in normalized:
        return DropReason.CONTROL_CHARACTER
    return _relative_to_repository(normalized, analysis_root, repository_root)


def _classify_unusable(normalized: str) -> DropReason | None:
    if _DRIVE.match(normalized):
        return DropReason.DRIVE_LETTER
    if normalized.startswith("//"):
        return DropReason.UNC_PATH
    if normalized.startswith("/"):
        return DropReason.ABSOLUTE_PATH
    if normalized == ".." or normalized.startswith("../"):
        return DropReason.PARENT_REFERENCE
    if normalized == ".":
        return DropReason.NOT_A_FILE
    return None


def _relative_to_analysis_root(file_path: str, analysis_root: Path) -> str | DropReason:
    if not file_path.startswith("/"):
        return DropReason.MALFORMED_URI
    if _WINDOWS_FILE_PATH.match(file_path):
        # file:///C:/x のパスは /C:/x で届く。先頭の / を除くと Windows の根と比べられる
        file_path = file_path[1:]
    inside = _inside(posixpath.normpath(file_path), analysis_root.as_posix())
    return DropReason.OUTSIDE_ANALYSIS_ROOT if inside is None else inside


def _relative_to_repository(
    relative: str, analysis_root: Path, repository_root: Path
) -> str | DropReason:
    # 根の名前を含む `..` を相殺するため、文字列の相対のまま繋がず、絶対パスにしてから比べる
    absolute = posixpath.normpath(posixpath.join(analysis_root.as_posix(), relative))
    inside = _inside(absolute, repository_root.as_posix())
    return DropReason.OUTSIDE_REPOSITORY if inside is None else inside


def _inside(absolute: str, root: str) -> str | None:
    """正規化済みの絶対パスの、根からの相対パス。根の下になければ None。"""
    prefix = root if root.endswith("/") else root + "/"
    if is_windows():
        # Windows のパスは大文字小文字を区別しない
        starts_inside = absolute.casefold().startswith(prefix.casefold())
    else:
        starts_inside = absolute.startswith(prefix)
    return absolute[len(prefix) :] if starts_inside else None
