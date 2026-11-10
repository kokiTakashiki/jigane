from language_plugin_support import builtin_states_without_grammar, external_plugin

from jigane.languages import registry
from jigane.languages.states import (
    LanguageGrammarAbsent,
    LanguageNotEnabled,
    LanguageReady,
    LanguageUnavailable,
    ReasonKind,
)


def _builtin_ready() -> tuple[LanguageReady, ...]:
    return tuple(LanguageReady(plugin) for plugin in registry.builtin_plugins())


def test_ready_builtin_language_returns_its_ready_state() -> None:
    registry.set_states(_builtin_ready())
    found = registry.language_for_suffix("src/a.py")
    assert isinstance(found, LanguageReady)
    assert found.plugin.adapter.name == "python"


def test_builtin_language_without_grammar_returns_grammar_absent() -> None:
    registry.set_states(builtin_states_without_grammar("swift"))
    found = registry.language_for_suffix("A.swift")
    assert isinstance(found, LanguageGrammarAbsent)
    assert found.name == "swift"
    assert registry.adapter_for("A.swift") is None


def test_ready_external_language_returns_its_ready_state() -> None:
    registry.set_states((*_builtin_ready(), LanguageReady(external_plugin("zig", ".zig"))))
    found = registry.language_for_suffix("a/b.ZIG")
    assert isinstance(found, LanguageReady)
    assert found.plugin.adapter.name == "zig"


def test_external_language_without_grammar_returns_grammar_absent() -> None:
    absent = LanguageGrammarAbsent(external_plugin("zig", ".zig"), "文法がありません")
    registry.set_states((*_builtin_ready(), absent))
    assert registry.language_for_suffix("a.zig") is absent
    assert registry.adapter_for("a.zig") is None


def test_unavailable_language_returns_none() -> None:
    faulty = LanguageUnavailable("zig", ReasonKind.GRAMMAR_ERROR, "sha256 が合いません")
    registry.set_states((*_builtin_ready(), faulty))
    assert registry.language_for_suffix("a.zig") is None


def test_unmatched_suffix_and_not_enabled_language_return_none() -> None:
    not_enabled = LanguageNotEnabled("kotlin", "jigane-kotlin")
    registry.set_states((*_builtin_ready(), not_enabled))
    assert registry.language_for_suffix("a.kt") is None
    assert registry.language_for_suffix("Makefile") is None
