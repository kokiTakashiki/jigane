from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from types import MappingProxyType

from jigane.languages.base import LanguageAdapter


@dataclass(frozen=True, order=True)
class ApiVersion:
    major: int
    minor: int


PLUGIN_API_VERSION = ApiVersion(1, 0)


@dataclass(frozen=True)
class Builtin:
    """jigane の本体に同梱された言語。"""


@dataclass(frozen=True)
class Distribution:
    """外のパッケージ（配布名と版）から来た言語。"""

    name: str
    version: str


Origin = Builtin | Distribution


@dataclass(frozen=True)
class GrammarSpec:
    pack_version: str
    # platform の名前（macos-arm64 など）から sha256 への対応
    hashes: Mapping[str, str]

    def __post_init__(self) -> None:
        # frozen でも辞書の中身は書き換えられるため、読み取り専用の写しに差し替える
        object.__setattr__(self, "hashes", MappingProxyType(dict(self.hashes)))


@dataclass(frozen=True)
class LanguagePlugin:
    origin: Origin
    api_version: ApiVersion
    adapter: LanguageAdapter
    # 利用者に見せる言語名
    label: str
    grammar: GrammarSpec
    data: Traversable | None
