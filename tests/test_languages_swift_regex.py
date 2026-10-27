import pytest

from jigane.languages.base import RegexAdapter, RegexDialect, RegexScan, RegexUse, parse
from jigane.languages.swift import SwiftAdapter

ADAPTER = SwiftAdapter()


def scan(source_text: str) -> RegexScan:
    source = source_text.encode()
    tree = parse(ADAPTER, source)
    return ADAPTER.regex_literals(tree.root_node, source, "A.swift")


def patterns(source_text: str) -> list[tuple[str, str]]:
    return [(literal.call, literal.pattern) for literal in scan(source_text).literals]


def test_adapter_satisfies_regex_protocol() -> None:
    assert isinstance(ADAPTER, RegexAdapter)


def test_regex_values() -> None:
    result = scan("let a = /a+\\d/\nlet b = #/x\\/y/#\n")
    assert [(r.call, r.pattern, r.dialect, r.line) for r in result.literals] == [
        ("/.../", "a+\\d", RegexDialect.SWIFT, 1),
        ("/.../", "x\\/y", RegexDialect.SWIFT, 2),
    ]


def test_regex_initializer_and_try() -> None:
    assert patterns('let a = Regex("a+")\nlet b = try Regex("b")\n') == [
        ("Regex(...)", "a+"),
        ("Regex(...)", "b"),
    ]


def test_ns_regular_expression_pattern_label() -> None:
    source = 'let a = try NSRegularExpression(pattern: "a", options: [])\n'
    assert patterns(source) == [("NSRegularExpression", "a")]


def test_constant_concatenation_is_literal() -> None:
    assert patterns('let a = Regex("a" + "b")\n') == [("Regex(...)", "ab")]


def test_composed_sites() -> None:
    result = scan(
        'let a = Regex(name)\nlet b = try NSRegularExpression(pattern: "a\\(x)")\n'
        'let c = Regex("a" + x)\nlet d = Regex(String(format: "%@", x))\n'
    )
    assert result.literals == ()
    assert [(c.call, c.line) for c in result.composed] == [
        ("Regex(...)", 1),
        ("NSRegularExpression", 2),
        ("Regex(...)", 3),
        ("Regex(...)", 4),
    ]


def test_document_order() -> None:
    result = scan("let a = Regex(x)\nlet b = /b/\nlet c = Regex(y)\n")
    assert [c.line for c in result.composed] == [1, 3]
    assert [r.line for r in result.literals] == [2]


def test_not_picked() -> None:
    source = (
        'let a = Other("a+")\n'
        "let b = NSRegularExpression(options: [])\n"
        'let c = Regex(verbatim: "a+")\n'
        'let d = "a+"\n'
        "let e = 4 / 2\n"
        'print("a")\n'
    )
    result = scan(source)
    assert result.literals == ()
    assert result.composed == ()


def test_string_escapes() -> None:
    assert patterns('let a = Regex("\\\\d\\"\\t\\u{41}")\n') == [("Regex(...)", '\\d"\tA')]


def test_extended_delimiter_string() -> None:
    source = 'let a = Regex(#"a\\d\\#n"#)\nlet b = NSRegularExpression(pattern: ##"(?<n>a)"##)\n'
    assert patterns(source) == [("Regex(...)", "a\\d\n"), ("NSRegularExpression", "(?<n>a)")]


def test_extended_delimiter_interpolation_is_composed() -> None:
    result = scan('let a = Regex(#"a\\#(x)"#)\n')
    assert result.literals == ()
    assert [c.call for c in result.composed] == ["Regex(...)"]


def test_every_site_is_compile_use() -> None:
    result = scan(
        'let a = /a/\nlet b = Regex("b")\n'
        'let c = try NSRegularExpression(pattern: "c", options: [])\nlet d = Regex(x)\n'
    )
    assert {r.use for r in result.literals} == {RegexUse.COMPILE}
    assert {r.use for r in result.composed} == {RegexUse.COMPILE}
    assert len(result.literals) == 3


def test_multiline_string_strips_closing_indent() -> None:
    source = 'let a = try Regex("""\n    a+\\\\d\n      b\n\n    """)\n'
    assert patterns(source) == [("Regex(...)", "a+\\d\n  b\n")]


def test_multiline_line_continuation_joins_lines() -> None:
    source = (
        'let a = try NSRegularExpression(pattern: """\n    a\\\n    b\n    """, options: [])\n'
    )
    assert patterns(source) == [("NSRegularExpression", "ab")]


def test_multiline_extended_delimiter() -> None:
    source = 'let a = Regex(#"""\n    a\\d "q"\n    """#)\n'
    assert patterns(source) == [("Regex(...)", 'a\\d "q"')]


def test_multiline_interpolation_is_composed() -> None:
    source = (
        'let a = Regex("""\n    a\\(x)\n    """)\nlet b = Regex(#"""\n    a\\#(x)\n    """#)\n'
    )
    result = scan(source)
    assert result.literals == ()
    assert [c.line for c in result.composed] == [1, 4]


def test_multiline_with_broken_grammar_is_composed() -> None:
    # `\d` は Swift では不正なエスケープで、文法が ERROR 節点を出す
    result = scan('let a = Regex("""\n    a+\\d\n    """)\n')
    assert result.literals == ()
    assert len(result.composed) == 1


def test_module_qualified_calls() -> None:
    source = (
        'let a = Foundation.NSRegularExpression(pattern: "a")\n'
        'let b = try _StringProcessing.Regex("b")\n'
    )
    assert patterns(source) == [("NSRegularExpression", "a"), ("Regex(...)", "b")]


def test_local_constant_is_followed_one_step() -> None:
    found = scan(
        'func f() {\n  let p = "a+" + "b"\n  _ = Regex(p)\n  var q = "c"\n'
        "  _ = try NSRegularExpression(pattern: q)\n}\n"
    )
    assert [(x.call, x.pattern) for x in found.literals] == [
        ("Regex(...)", "a+b"),
        ("NSRegularExpression", "c"),
    ]
    assert found.composed == ()


def test_local_constant_is_not_followed_across_two_steps() -> None:
    found = scan('func f() {\n  let p = "a"\n  let q = p\n  _ = Regex(q)\n}\n')
    assert found.literals == ()
    assert len(found.composed) == 1


@pytest.mark.parametrize(
    "body",
    [
        'var p = "a"\n  p = "b"\n  _ = Regex(p)',
        'var p = "a"\n  p += "b"\n  _ = Regex(p)',
        'var p: String\n  if c { p = "a" } else { p = "b" }\n  _ = Regex(p)',
        'if c { let p = "a"\n  _ = Regex(p) }',
        "for p in list { _ = Regex(p) }",
        '_ = Regex(p)\n  let p = "a"',
    ],
)
def test_local_constant_is_not_followed(body: str) -> None:
    found = scan(f"func f() {{\n  {body}\n}}\n")
    assert found.literals == ()
    assert len(found.composed) == 1


def test_parameter_is_not_followed() -> None:
    found = scan("func f(p: String) {\n  _ = Regex(p)\n}\n")
    assert found.literals == ()
    assert len(found.composed) == 1


def test_call_without_argument_list_is_not_a_site() -> None:
    result = scan("let c = Regex { x }\n")
    assert result.composed == ()
    assert result.literals == ()


def test_constant_shapes_are_pinned() -> None:
    source = (
        'let a = Regex(("a"))\n'
        'let b = Regex("a" - "b")\n'
        'let d = NSRegularExpression(pattern: ("p"))\n'
    )
    result = scan(source)
    assert [(r.call, r.pattern) for r in result.literals] == [
        ("Regex(...)", "a"),
        ("NSRegularExpression", "p"),
    ]
    assert [c.line for c in result.composed] == [2]
