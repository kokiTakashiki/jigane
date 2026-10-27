import importlib
import json

import pytest

from jigane.languages import grammars, registry
from jigane.languages.api import ApiVersion, Builtin, GrammarSpec, LanguagePlugin

WINDOWS = ("jigane.languages.api", "jigane.languages.api.capabilities")


@pytest.mark.parametrize("module_name", WINDOWS)
def test_every_name_in_all_is_importable(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert module.__all__
    for name in module.__all__:
        assert hasattr(module, name), name


def test_builtin_plugins_declare_the_four_languages() -> None:
    plugins = registry.builtin_plugins()
    assert [plugin.adapter.name for plugin in plugins] == ["python", "java", "swift", "csharp"]
    assert [plugin.label for plugin in plugins] == ["Python", "Java", "Swift", "C#"]
    for plugin in plugins:
        assert isinstance(plugin, LanguagePlugin)
        assert plugin.origin == Builtin()
        assert plugin.api_version == ApiVersion(1, 0)
        assert plugin.data is None


def test_builtin_grammar_spec_matches_the_recorded_hashes() -> None:
    recorded = json.loads(grammars.HASHES_PATH.read_text(encoding="utf-8"))
    for plugin in registry.builtin_plugins():
        spec = plugin.grammar
        assert isinstance(spec, GrammarSpec)
        assert spec.pack_version == grammars.pack_version()
        grammar = plugin.adapter.grammar
        # 文法の記録が無い platform は、registry と同じく対応に含めない
        expected = {
            platform: names[grammar] for platform, names in recorded.items() if grammar in names
        }
        assert dict(spec.hashes) == expected


def test_grammar_spec_hashes_are_read_only() -> None:
    source = {"macos-arm64": "a" * 64}
    spec = GrammarSpec(pack_version="1", hashes=source)
    source["linux-x86_64"] = "b" * 64
    assert dict(spec.hashes) == {"macos-arm64": "a" * 64}
    with pytest.raises(TypeError):
        spec.hashes["linux-x86_64"] = "c" * 64  # type: ignore[index]


def test_label_for_falls_back_to_the_adapter_name() -> None:
    assert registry.label_for("csharp") == "C#"
    assert registry.label_for("unknown") == "unknown"


def test_api_version_is_ordered_by_major_then_minor() -> None:
    assert ApiVersion(1, 0) < ApiVersion(1, 1) < ApiVersion(2, 0)
    assert ApiVersion(1, 0) == ApiVersion(1, 0)
