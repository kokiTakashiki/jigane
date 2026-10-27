import pytest

from jigane.languages.base import RegexAdapter, RegexDialect, RegexScan, RegexUse, parse
from jigane.languages.java import JavaAdapter

adapter = JavaAdapter()


def scan(body: str) -> RegexScan:
    source = f"class A {{\n  void f() {{\n{body}\n  }}\n}}\n".encode()
    return adapter.regex_literals(parse(adapter, source).root_node, source, "A.java")


def patterns(body: str) -> list[tuple[str, str]]:
    return [(found.call, found.pattern) for found in scan(body).literals]


def test_adapter_satisfies_the_protocol() -> None:
    assert isinstance(adapter, RegexAdapter)


def test_pattern_compile_and_matches() -> None:
    body = 'Pattern.compile("a+"); Pattern.matches("b", s);'
    assert patterns(body) == [("Pattern.compile", "a+"), ("Pattern.matches", "b")]


def test_fully_qualified_pattern() -> None:
    assert patterns('java.util.regex.Pattern.compile("a");') == [("Pattern.compile", "a")]


def test_pattern_compile_with_flags_uses_first_argument() -> None:
    assert patterns('Pattern.compile("a", Pattern.CASE_INSENSITIVE);') == [
        ("Pattern.compile", "a")
    ]


@pytest.mark.parametrize("method", ["matches", "replaceAll", "replaceFirst", "split"])
def test_string_methods(method: str) -> None:
    assert patterns(f's.{method}("ab+");') == [(f"String.{method}", "ab+")]


def test_dialect_path_and_line() -> None:
    found = scan('Pattern.compile("a");').literals[0]
    assert found.dialect is RegexDialect.JAVA
    assert found.path == "A.java"
    assert found.line == 3


def test_single_character_split_is_ignored() -> None:
    assert patterns('s.split(","); s.split("\\\\|"); s.split(", ");') == [
        ("String.split", "\\|"),
        ("String.split", ", "),
    ]


def test_variable_argument_of_string_methods_is_dropped() -> None:
    found = scan("s.matches(p); s.replaceAll(p, r); s.replaceFirst(p, r); s.split(p);")
    assert found.literals == ()
    assert found.composed == ()


def test_composed_pattern_class_calls() -> None:
    found = scan(
        'Pattern.compile(p); Pattern.matches("a" + p, s); Pattern.compile(String.format("%s", p));'
    )
    assert found.literals == ()
    assert [(c.call, c.line) for c in found.composed] == [
        ("Pattern.compile", 3),
        ("Pattern.matches", 3),
        ("Pattern.compile", 3),
    ]


def test_plus_concatenation() -> None:
    assert patterns('Pattern.compile("a" + "b" + ("c" + "d"));') == [("Pattern.compile", "abcd")]


def test_unrelated_calls_are_ignored() -> None:
    body = 'Other.compile("a"); s.contains("a"); s.replace("a", "b"); compile("a"); matches("a");'
    found = scan(body)
    assert found.literals == ()
    assert found.composed == ()


def test_escapes_are_decoded() -> None:
    assert patterns('Pattern.compile("\\\\d+\\t\\"\\u0041\\101");') == [
        ("Pattern.compile", '\\d+\t"AA')
    ]


def test_text_block_strips_indentation_and_decodes() -> None:
    body = 'Pattern.compile("""\n      a\\\\d\n        b\n      """);'
    assert patterns(body) == [("Pattern.compile", "a\\d\n  b\n")]


def test_text_block_without_trailing_newline_and_line_continuation() -> None:
    body = 'Pattern.compile("""\n    a\\\n    b""");'
    assert patterns(body) == [("Pattern.compile", "ab")]


def test_document_order() -> None:
    body = 'Pattern.compile("a"); s.split("bb"); Pattern.compile(p); Pattern.compile("c");'
    found = scan(body)
    assert [lit.pattern for lit in found.literals] == ["a", "bb", "c"]


def test_uses() -> None:
    result = scan(
        'Pattern.compile("ab"); Pattern.matches("ab", s); s.matches("ab");'
        ' s.replaceAll("ab", "c");'
        ' s.replaceFirst("ab", "c"); s.split("ab"); Pattern.compile(x);'
    )
    assert [(r.call, r.use) for r in result.literals] == [
        ("Pattern.compile", RegexUse.COMPILE),
        ("Pattern.matches", RegexUse.MATCH),
        ("String.matches", RegexUse.MATCH),
        ("String.replaceAll", RegexUse.REPLACE),
        ("String.replaceFirst", RegexUse.REPLACE),
        ("String.split", RegexUse.SPLIT),
    ]
    assert [(r.call, r.use) for r in result.composed] == [("Pattern.compile", RegexUse.COMPILE)]


def test_local_constant_is_followed_one_step() -> None:
    found = scan(
        'final String p = "a+" + "b";\nPattern.compile(p);\nString q = "c";\nx.matches(q);'
    )
    assert [(x.call, x.pattern) for x in found.literals] == [
        ("Pattern.compile", "a+b"),
        ("String.matches", "c"),
    ]
    assert found.composed == ()


def test_local_constant_is_not_followed_across_two_steps() -> None:
    found = scan('String p = "a";\nString q = p;\nPattern.compile(q);')
    assert found.literals == ()
    assert len(found.composed) == 1


@pytest.mark.parametrize(
    "body",
    [
        'String p = "a";\np = "b";\nPattern.compile(p);',
        'String p = "a";\np += "b";\nPattern.compile(p);',
        'String p;\nif (c) { p = "a"; }\nPattern.compile(p);',
        'if (c) { String p = "a"; Pattern.compile(p); }',
        "for (String p : list) { Pattern.compile(p); }",
        'Pattern.compile(p);\nString p = "a";',
    ],
)
def test_local_constant_is_not_followed(body: str) -> None:
    found = scan(body)
    assert found.literals == ()
    assert len(found.composed) == 1


def test_parameter_is_not_followed() -> None:
    source = b"class A {\n  void f(String p) {\n    Pattern.compile(p);\n  }\n}\n"
    found = adapter.regex_literals(parse(adapter, source).root_node, source, "A.java")
    assert found.literals == ()
    assert len(found.composed) == 1


def test_non_plus_binary_expression_is_not_a_constant() -> None:
    result = scan('Pattern.compile("a" - "b");')
    assert result.literals == ()
    assert [(c.call, c.line) for c in result.composed] == [("Pattern.compile", 3)]


def test_non_plus_binary_expression_in_a_string_method_is_not_a_constant() -> None:
    # `+` の連結なら定数として拾われる。`*` が拾われないことで、演算子の選り分けを確かめる。
    assert patterns('"x".matches("a" + "b");') == [("String.matches", "ab")]
    result = scan('"x".matches("a" * "b");')
    assert result.literals == ()
    # String の呼び出しは、定数でない引数を composed にも数えない
    assert result.composed == ()
