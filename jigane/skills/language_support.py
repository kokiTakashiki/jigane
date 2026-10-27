"""言語ごとの対応の水準。アダプタが満たす口と、方針の資料の図のどれに対応するかを求める。

方針の資料の図の登録簿に依るので、言語の層ではなくここ（資料の層）に置く。

口は試験的な段で、jigane の版で変わりうる。口の一覧は `jigane.languages.api.capabilities` の
Protocol から、図の一覧は `REGISTRY` から導き、ここには直書きしない。
"""

from dataclasses import dataclass

from jigane.languages.api import capabilities
from jigane.languages.base import LanguageAdapter
from jigane.languages.plugin import LanguagePlugin
from jigane.skills.policy_figures import LANGUAGE_AGNOSTIC, REGISTRY, FigureKey


def _interfaces() -> tuple[type, ...]:
    found = [
        value
        for value in vars(capabilities).values()
        if isinstance(value, type)
        and getattr(value, "_is_protocol", False)
        and value is not LanguageAdapter
        and LanguageAdapter in value.__mro__
    ]
    return tuple(sorted(found, key=lambda interface: interface.__name__))


INTERFACES: tuple[type, ...] = _interfaces()


@dataclass(frozen=True)
class InterfaceSupport:
    """口一つへの対応。interface は Protocol の名前。"""

    interface: str
    supported: bool


@dataclass(frozen=True)
class FigureSupport:
    """図一つへの対応。"""

    figure: FigureKey
    supported: bool


@dataclass(frozen=True)
class LanguageSupport:
    """一つの言語の対応の水準。資料（照会と flaky の型）の欄は、置き場が決まる段で足す。"""

    interfaces: tuple[InterfaceSupport, ...]
    figures: tuple[FigureSupport, ...]

    def supported_figures(self) -> tuple[FigureKey, ...]:
        return tuple(item.figure for item in self.figures if item.supported)

    def unsupported_figures(self) -> tuple[FigureKey, ...]:
        return tuple(item.figure for item in self.figures if not item.supported)

    def to_json(self) -> dict[str, dict[str, bool]]:
        return {
            "interfaces": {item.interface: item.supported for item in self.interfaces},
            "figures": {item.figure: item.supported for item in self.figures},
        }


def support_of(plugin: LanguagePlugin) -> LanguageSupport:
    adapter = plugin.adapter
    interfaces = tuple(
        InterfaceSupport(interface.__name__, isinstance(adapter, interface))
        for interface in INTERFACES
    )
    figures = tuple(
        FigureSupport(figure.key, isinstance(adapter, figure.interface))
        for figure in REGISTRY
        # 言語に依らない図は全ての言語が描けるので、水準に数えない
        if figure.interface is not LANGUAGE_AGNOSTIC
    )
    return LanguageSupport(interfaces=interfaces, figures=figures)
