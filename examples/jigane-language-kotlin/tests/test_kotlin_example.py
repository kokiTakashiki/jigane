"""例のパッケージの試験。Kotlin の文法は `make setup` が取得済みで、試験の中では取得しない。"""

from importlib import metadata

import pytest
from jigane_language_kotlin import KotlinAdapter, plugin
from tree_sitter import Parser

from jigane.languages import grammars
from jigane.languages.api import Declaration, LanguageAdapter, Visibility

SOURCE = b"""package a.b

import x.y.Z

// line
/* block */
public class Outer(val q: Int) {
    private val hidden: Int = 1
    var shown = 2
    fun work(a: Int): Int {
        return a
    }
    internal fun inner() = 3
    companion object {
        fun make() {}
    }
    class Nested
}

object Single

interface Api {
    fun call(): Int
}

enum class Color { RED, GREEN }

fun top() {}

private val secret = 3
"""


@pytest.fixture(scope="module")
def parser() -> Parser:
    try:
        return grammars.get_parser(grammars.external_target(plugin()))
    except grammars.GrammarError as error:
        # 取得は make setup だけで行う。無い前提のまま skip で通さず、失敗させて案内する
        pytest.fail(f"Kotlin の文法を使えない。`make setup` を走らせてください: {error}")


@pytest.fixture(scope="module")
def declarations(parser: Parser) -> dict[str, Declaration]:
    tree = parser.parse(SOURCE)
    found = KotlinAdapter().declarations(tree.root_node, SOURCE)
    return {d.qualified_name: d for d in found}


def test_plugin_reads_its_own_distribution() -> None:
    result = plugin()
    assert result.origin.name == "jigane-language-kotlin"  # type: ignore[union-attr]
    assert result.origin.version == metadata.version("jigane-language-kotlin")  # type: ignore[union-attr]
    assert result.label == "Kotlin"
    assert isinstance(result.adapter, LanguageAdapter)
    assert result.adapter.suffixes == frozenset({".kt", ".kts"})


def test_types_and_members_are_listed(declarations: dict[str, Declaration]) -> None:
    assert {
        "Outer",
        "Outer.hidden",
        "Outer.shown",
        "Outer.work",
        "Outer.inner",
        "Outer.Companion",
        "Outer.Companion.make",
        "Outer.Nested",
        "Single",
        "Api",
        "Api.call",
        "Color",
        "Color.RED",
        "top",
        "secret",
    } <= set(declarations)


def test_kinds_distinguish_functions_from_methods(declarations: dict[str, Declaration]) -> None:
    assert declarations["top"].kind == "function"
    assert declarations["Outer.work"].kind == "method"
    assert declarations["Outer.shown"].kind == "variable"
    assert declarations["Outer"].kind == "class"


def test_visibility_follows_modifiers(declarations: dict[str, Declaration]) -> None:
    assert declarations["Outer.hidden"].visibility is Visibility.FILE
    assert declarations["secret"].visibility is Visibility.FILE
    assert declarations["Outer.inner"].visibility is Visibility.INTERNAL
    assert declarations["Outer.work"].visibility is Visibility.PUBLIC
    assert declarations["top"].visibility is Visibility.PUBLIC


def test_spans_split_signature_and_body(declarations: dict[str, Declaration]) -> None:
    work = declarations["Outer.work"]
    assert (work.span.start_line, work.span.end_line) == (10, 12)
    assert (work.signature.start_line, work.signature.end_line) == (10, 10)
    assert work.body is not None
    assert (work.body.start_line, work.body.end_line) == (11, 12)
    assert declarations["Outer.Nested"].body is None


CAPPED = b"""private class Hidden {
    fun reach() {}
    class Deep {
        fun deeper() {}
    }
}

internal class Mid {
    public fun wide() {}
}

open class Base {
    protected fun guarded() {}
    protected val guardedValue = 1
}

private enum class Level { LOW, HIGH }

object Body {
    fun run() {}
}
"""


@pytest.fixture(scope="module")
def capped(parser: Parser) -> dict[str, Declaration]:
    tree = parser.parse(CAPPED)
    found = KotlinAdapter().declarations(tree.root_node, CAPPED)
    return {d.qualified_name: d for d in found}


def test_member_is_never_wider_than_its_enclosing_type(capped: dict[str, Declaration]) -> None:
    assert capped["Hidden.reach"].visibility is Visibility.FILE
    assert capped["Hidden.Deep"].visibility is Visibility.FILE
    assert capped["Hidden.Deep.deeper"].visibility is Visibility.FILE
    assert capped["Mid.wide"].visibility is Visibility.INTERNAL


def test_protected_is_listed_as_public(capped: dict[str, Declaration]) -> None:
    assert capped["Base.guarded"].visibility is Visibility.PUBLIC
    assert capped["Base.guardedValue"].visibility is Visibility.PUBLIC


def test_enum_entry_takes_the_visibility_of_its_enum(capped: dict[str, Declaration]) -> None:
    assert capped["Level.LOW"].visibility is Visibility.FILE
    assert capped["Level.HIGH"].visibility is Visibility.FILE


def test_top_level_object_with_body_is_listed_with_its_members(
    capped: dict[str, Declaration],
) -> None:
    # 複数行で書いた本体を持つトップレベルの object は object_declaration になり拾える。
    # 一行で書くと式に誤解析される（README の既知の限界）
    assert capped["Body"].kind == "class"
    assert capped["Body.run"].kind == "method"


def test_declared_platforms_match_the_builtin_record() -> None:
    assert set(plugin().grammar.hashes) == set(grammars.recorded_hashes())
