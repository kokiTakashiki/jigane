"""外部プラグインが実装したインターフェースが、registry の実経路で効くことの試験。"""

import inspect
import json
from pathlib import Path

import pytest
from language_plugin_support import Installer, approved

from jigane import commands
from jigane.languages import base, registry
from jigane.technology_list import build

_INTERFACES = tuple(
    value
    for _, value in inspect.getmembers(base, inspect.isclass)
    if getattr(value, "_is_protocol", False) and getattr(value, "_is_runtime_protocol", False)
)
_LANGUAGE_MEMBERS = base.LanguageAdapter.__protocol_attrs__  # type: ignore[attr-defined]


def _members_of(interface: type) -> str:
    names = sorted(interface.__protocol_attrs__ - _LANGUAGE_MEMBERS)  # type: ignore[attr-defined]
    # 値が None のメソッドは、runtime_checkable の isinstance が未実装として扱う
    return "".join(
        f"\n    def {name}(self, *args, **kwargs):\n        return None\n" for name in names
    )


def _enable(install: Installer, tmp_path: Path, members: str) -> None:
    # 文法は組み込みの python を指す。照合は組み込みの記録で通る
    install("zig", suffixes=(".zig",), members=members)
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "languages.json").write_text(
        json.dumps({"enabled": [approved("zig").to_json()]}), encoding="utf-8"
    )
    registry.set_states(None)


def test_the_interface_list_is_the_runtime_protocols() -> None:
    assert {interface.__name__ for interface in _INTERFACES} == {
        "LanguageAdapter",
        "SequenceAdapter",
        "DiscriminatorBranchExtractor",
        "ConcurrencyAdapter",
        "MarkupEventHandlerExtractor",
        "ImportedModuleExtractor",
        "CommandArgumentExtractor",
        "FilePathExpressionExtractor",
        "StoreAccessExtractor",
        "AddedPublicNameFinder",
        "RegexSiteExtractor",
        "StringLiteralSetExtractor",
        "StringLiteralComparisonExtractor",
        "TypeFieldNameExtractor",
        "TopLevelStringConstantExtractor",
        "UnsupportedSyntaxDetector",
    }


@pytest.mark.parametrize("interface", _INTERFACES, ids=lambda interface: interface.__name__)
def test_external_adapter_satisfies_the_interface_through_the_registry(
    install: Installer, tmp_path: Path, interface: type
) -> None:
    _enable(install, tmp_path, _members_of(interface))
    adapter = registry.adapter_for("a.zig")
    assert adapter is not None
    assert isinstance(adapter, interface)


@pytest.mark.parametrize("interface", _INTERFACES, ids=lambda interface: interface.__name__)
def test_external_adapter_without_members_does_not_satisfy_the_other_interfaces(
    install: Installer, tmp_path: Path, interface: type
) -> None:
    _enable(install, tmp_path, "")
    adapter = registry.adapter_for("a.zig")
    assert adapter is not None
    assert isinstance(adapter, interface) == (interface is base.LanguageAdapter)


_IMPORT_MEMBERS = """
    def imported_modules(self, root_node, source):
        from jigane.languages.base import ImportedModule

        return [ImportedModule("alpha.beta", 1), ImportedModule(".local", 2)]

    def technology_name_of(self, module):
        return module.split(".")[0].upper()
"""


def test_technology_list_uses_the_import_interface_of_an_external_plugin(
    install: Installer, tmp_path: Path
) -> None:
    _enable(install, tmp_path, _IMPORT_MEMBERS)
    (tmp_path / "a.zig").write_text("x = 1\n", encoding="utf-8")
    usages = build._usages(tmp_path, ["a.zig"])
    assert list(usages) == [("zig", "ALPHA")]


@pytest.mark.parametrize("implemented", [False, True])
def test_languages_list_marks_the_figure_by_the_interface_of_an_external_plugin(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str], *, implemented: bool
) -> None:
    _enable(install, tmp_path, _members_of(base.SequenceAdapter) if implemented else "")
    assert commands.main(["languages", "list"]) == 0
    zig_lines = capsys.readouterr().out.split("zig\t", 1)[1].splitlines()
    unsupported = next(line for line in zig_lines if "対応外の図:" in line)
    assert ("シーケンス図" not in unsupported) == implemented


def test_external_plugin_with_the_member_is_an_unsupported_syntax_detector(
    install: Installer, tmp_path: Path
) -> None:
    _enable(install, tmp_path, _members_of(base.UnsupportedSyntaxDetector))
    adapter = registry.adapter_for("a.zig")
    assert isinstance(adapter, base.UnsupportedSyntaxDetector)
