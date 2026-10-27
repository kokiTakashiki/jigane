from dataclasses import replace
from pathlib import Path

import pytest
from tree_sitter import Node

from jigane import commands
from jigane.commands import failures
from jigane.languages import registry
from jigane.languages.base import Declaration
from jigane.languages.plugin import Distribution
from jigane.languages.plugins import LanguageReady, LanguageUnavailable, ReasonKind


class _FakeAdapter:
    grammar = "python"
    comment_node_types: frozenset[str] = frozenset()
    import_node_types: frozenset[str] = frozenset()
    test_directory_names: tuple[str, ...] = ("extspecs",)
    test_file_patterns: tuple[str, ...] = ()
    build_file_patterns: tuple[str, ...] = ("ext.build",)

    def __init__(self, name: str, suffixes: frozenset[str]) -> None:
        self.name = name
        self.suffixes = suffixes

    def declarations(self, _root_node: Node, _source: bytes) -> list[Declaration]:
        return []


def _external(name: str, suffixes: set[str]) -> LanguageReady:
    base = registry.builtin_plugins()[0]
    plugin = replace(
        base,
        origin=Distribution(name=f"jigane-{name}", version="1.0"),
        adapter=_FakeAdapter(name, frozenset(suffixes)),
        label=name.upper(),
    )
    return LanguageReady(plugin)


def _builtin_ready() -> list[LanguageReady]:
    return [LanguageReady(plugin) for plugin in registry._plugins]


def test_external_ready_language_is_found_by_suffix() -> None:
    registry.set_states((*_builtin_ready(), _external("zig", {".zig"})))
    adapter = registry.adapter_for("src/a.zig")
    assert adapter is not None
    assert adapter.name == "zig"
    assert registry.label_for("zig") == "ZIG"
    assert registry.is_test_path("extspecs/x.zig", registry.adapters())
    assert registry.is_build_path("ext.build", registry.adapters())


def test_builtin_wins_on_overlapping_suffix() -> None:
    registry.set_states((*_builtin_ready(), _external("snake", {".py", ".snk"})))
    adapter = registry.adapter_for("a.py")
    assert adapter is not None
    assert adapter.name == "python"
    snake = registry.adapter_for("a.snk")
    assert snake is not None
    assert snake.name == "snake"


def test_builtins_listed_first() -> None:
    registry.set_states((*_builtin_ready(), _external("zig", {".zig"})))
    names = [adapter.name for adapter in registry.adapters()]
    assert names[-1] == "zig"
    assert names[:-1] == [plugin.adapter.name for plugin in registry._plugins]


def test_states_without_externals_are_only_builtins(monkeypatch: pytest.MonkeyPatch) -> None:
    # 開発の環境には例のパッケージの entry point が載っている。外の言語が無い環境にするため空にする
    monkeypatch.setattr(registry, "entry_points", lambda **_kwargs: [])
    assert [type(state) for state in registry.states()] == [LanguageReady] * len(registry._plugins)
    assert registry.adapter_for("a.zig") is None


def test_main_stops_on_failed_external(capsys: pytest.CaptureFixture[str]) -> None:
    failed = LanguageUnavailable("zig", ReasonKind.LOAD_ERROR, "ImportError: boom")
    registry.set_states((*_builtin_ready(), failed))
    assert commands.main(["list"]) != 0
    err = capsys.readouterr().err
    assert "zig" in err
    assert "load_error" in err
    assert "ImportError: boom" in err
    assert "jigane languages disable zig" in err


def test_main_stops_on_broken_enabled_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "languages.json").write_text("{not json", encoding="utf-8")
    assert commands.main(["list"]) != 0
    assert "languages.json" in capsys.readouterr().err


def test_unavailable_error_has_its_own_kind() -> None:
    failure = failures.describe(registry.LanguagePluginUnavailableError("x"))
    assert failure.kind == "language_plugin_unavailable"
