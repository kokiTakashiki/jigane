"""文書を渡さない理由。理由ごとの型の和で、付随するデータはその型だけが持つ。

型ごとに runs.json の語（word）と警告の文（sentence）を持つ。語に付随するデータは残さない。
"""

from dataclasses import dataclass
from typing import ClassVar

MAX_DOCUMENT_BYTES = 32_768


@dataclass(frozen=True)
class NotAbsolute:
    word: ClassVar[str] = "not_absolute"
    sentence: ClassVar[str] = "絶対パスではありません"


@dataclass(frozen=True)
class SecretName:
    word: ClassVar[str] = "secret_name"
    sentence: ClassVar[str] = "秘密のファイル名に当たります"


@dataclass(frozen=True)
class Missing:
    word: ClassVar[str] = "missing"
    sentence: ClassVar[str] = "ファイルがありません"


@dataclass(frozen=True)
class NotInBase:
    word: ClassVar[str] = "not_in_base"
    sentence: ClassVar[str] = "レビューの起点にファイルがありません"


@dataclass(frozen=True)
class LinkInBase:
    word: ClassVar[str] = "link_in_base"
    sentence: ClassVar[str] = "レビューの起点でシンボリックリンクです"


@dataclass(frozen=True)
class NotRegularFile:
    word: ClassVar[str] = "not_regular_file"
    sentence: ClassVar[str] = "通常のファイルではありません"


@dataclass(frozen=True)
class TooLarge:
    word: ClassVar[str] = "too_large"
    size_bytes: int

    @property
    def sentence(self) -> str:
        return f"本文が {MAX_DOCUMENT_BYTES} バイトを超えています（{self.size_bytes} バイト）"


@dataclass(frozen=True)
class NotUtf8:
    word: ClassVar[str] = "not_utf8"
    sentence: ClassVar[str] = "UTF-8 として読めません"


@dataclass(frozen=True)
class HasNul:
    word: ClassVar[str] = "has_nul"
    sentence: ClassVar[str] = "NUL を含みます"


@dataclass(frozen=True)
class Unreadable:
    word: ClassVar[str] = "unreadable"
    os_reason: str

    @property
    def sentence(self) -> str:
        return f"読めません（{self.os_reason}）"


SkipReason = (
    NotAbsolute
    | SecretName
    | Missing
    | NotInBase
    | LinkInBase
    | NotRegularFile
    | TooLarge
    | NotUtf8
    | HasNul
    | Unreadable
)


def describe(reason: SkipReason) -> str:
    """警告の文末に添える、渡さない理由の文。"""
    return reason.sentence


def record_word(reason: SkipReason) -> str:
    """runs.json の reason に残す語。"""
    return reason.word
