from dataclasses import dataclass
from enum import StrEnum

from jigane.languages.plugin import Distribution, LanguagePlugin


class ReasonKind(StrEnum):
    ENTRY_POINT_MISSING = "entry_point_missing"
    LOAD_ERROR = "load_error"
    VALIDATION_ERROR = "validation_error"
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


@dataclass(frozen=True)
class LanguageGrammarAbsent:
    """文法の共有ライブラリが置き場に無いだけの言語。他の言語は使えるので、起動は止めない。

    アダプタは読み込み済みで、名前、拡張子、配布元はプラグインから引く。
    """

    plugin: LanguagePlugin
    detail: str

    @property
    def name(self) -> str:
        return self.plugin.adapter.name

    @property
    def distribution(self) -> str | None:
        """追加の言語の配布名。組み込みの言語は None。"""
        origin = self.plugin.origin
        return origin.name if isinstance(origin, Distribution) else None

    @property
    def version(self) -> str | None:
        origin = self.plugin.origin
        return origin.version if isinstance(origin, Distribution) else None


LanguageState = LanguageReady | LanguageNotEnabled | LanguageGrammarAbsent | LanguageUnavailable
