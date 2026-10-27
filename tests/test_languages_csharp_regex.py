import pytest

from jigane.languages.base import RegexAdapter, RegexDialect, RegexScan, RegexUse, parse
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.csharp_regex import _LOCALS
from jigane.languages.regex_locals import local_value

ADAPTER = CSharpAdapter()


def scan(body: str) -> RegexScan:
    source = ("class A { void F() {\n" + body + "\n} }").encode()
    tree = parse(ADAPTER, source)
    return ADAPTER.regex_literals(tree.root_node, source, "A.cs")


def patterns(body: str) -> list[tuple[str, str]]:
    return [(literal.call, literal.pattern) for literal in scan(body).literals]


def test_adapter_satisfies_regex_protocol() -> None:
    assert isinstance(ADAPTER, RegexAdapter)


def test_new_regex_and_qualified_name() -> None:
    result = patterns(
        'var a = new Regex("a+");\nvar b = new System.Text.RegularExpressions.Regex("b");'
    )
    assert result == [("Regex", "a+"), ("Regex", "b")]


def test_static_methods() -> None:
    body = "\n".join(
        f'Regex.{m}(s, "p{i}");'
        for i, m in enumerate(["IsMatch", "Match", "Matches", "Replace", "Split"])
    )
    assert patterns(body) == [
        ("Regex.IsMatch", "p0"),
        ("Regex.Match", "p1"),
        ("Regex.Matches", "p2"),
        ("Regex.Replace", "p3"),
        ("Regex.Split", "p4"),
    ]


def test_static_method_pattern_is_second_argument() -> None:
    assert patterns('Regex.IsMatch(s, "a+");') == [("Regex.IsMatch", "a+")]
    assert patterns('Regex.Replace(s, "a+", "b", RegexOptions.None);') == [("Regex.Replace", "a+")]


def test_static_method_composed_second_argument() -> None:
    result = scan('Regex.IsMatch(s, pat);\nRegex.IsMatch("a", pat + "b");')
    assert result.literals == ()
    assert [(c.call, c.line) for c in result.composed] == [
        ("Regex.IsMatch", 2),
        ("Regex.IsMatch", 3),
    ]


def test_static_method_named_pattern() -> None:
    assert patterns('Regex.IsMatch(input: s, pattern: "a+");') == [("Regex.IsMatch", "a+")]
    assert patterns('Regex.IsMatch(pattern: "a+", input: s);') == [("Regex.IsMatch", "a+")]


def test_static_method_with_one_argument_is_not_picked() -> None:
    result = scan('Regex.IsMatch("a+");')
    assert result.literals == ()
    assert result.composed == ()


def test_generated_regex_attribute() -> None:
    source = (
        b'partial class A { [GeneratedRegex("a|b", RegexOptions.None)] '
        b"private static partial Regex R(); }"
    )
    tree = parse(ADAPTER, source)
    result = ADAPTER.regex_literals(tree.root_node, source, "A.cs")
    assert [(r.call, r.pattern, r.dialect) for r in result.literals] == [
        ("GeneratedRegex", "a|b", RegexDialect.DOTNET)
    ]


def test_line_numbers_are_one_based() -> None:
    result = scan('var a = new Regex("x");')
    assert result.literals[0].line == 2
    assert result.literals[0].path == "A.cs"


def test_constant_concatenation_is_literal() -> None:
    assert patterns('var a = new Regex("a" + "b" + @"c");') == [("Regex", "abc")]


def test_composed_sites() -> None:
    result = scan(
        'var a = new Regex(name);\nRegex.IsMatch(s, $"a{x}");\nvar b = new Regex("a" + x);\n'
        'var c = new Regex(string.Format("{0}", x));'
    )
    assert result.literals == ()
    assert [(c.call, c.line) for c in result.composed] == [
        ("Regex", 2),
        ("Regex.IsMatch", 3),
        ("Regex", 4),
        ("Regex", 5),
    ]


def test_document_order_across_kinds() -> None:
    result = scan('new Regex(a);\nnew Regex("b");\nnew Regex(c);')
    assert [c.line for c in result.composed] == [2, 4]
    assert [r.line for r in result.literals] == [3]


def test_not_picked() -> None:
    body = (
        'var r = new Other("a");\n'
        'regex.IsMatch("a");\n'
        'Regex.Escape("a");\n'
        's.Replace("a", "b");\n'
        "new Regex();\n"
        'var s = "a+";\n'
        'Console.WriteLine("a");'
    )
    result = scan(body)
    assert result.literals == ()
    assert result.composed == ()


def test_verbatim_string() -> None:
    assert patterns('new Regex(@"a""b\\d");') == [("Regex", 'a"b\\d')]


def test_regular_string_escapes() -> None:
    assert patterns(r'new Regex("\\d\"\t\u0041");') == [("Regex", '\\d"\tA')]


def test_raw_string_single_line() -> None:
    assert patterns('new Regex("""a"b\\d""");') == [("Regex", 'a"b\\d')]


def test_raw_string_multi_line_strips_indent() -> None:
    assert patterns('new Regex("""\n    a\n      b\n    """);') == [("Regex", "a\n  b")]


def test_uses() -> None:
    result = scan(
        'new Regex("ab"); Regex.IsMatch(s, "ab"); Regex.Match(s, "ab"); Regex.Matches(s, "ab");'
        ' Regex.Replace(s, "ab", "c"); Regex.Split(s, "ab"); new Regex(x);'
    )
    assert [(r.call, r.use) for r in result.literals] == [
        ("Regex", RegexUse.COMPILE),
        ("Regex.IsMatch", RegexUse.MATCH),
        ("Regex.Match", RegexUse.MATCH),
        ("Regex.Matches", RegexUse.MATCH),
        ("Regex.Replace", RegexUse.REPLACE),
        ("Regex.Split", RegexUse.SPLIT),
    ]
    assert [(r.call, r.use) for r in result.composed] == [("Regex", RegexUse.COMPILE)]


def test_local_constant_is_followed_one_step() -> None:
    found = scan(
        'var p = @"a+" + "b";\nRegex.IsMatch(s, p);\nconst string q = "c";\nnew Regex(q);'
    )
    assert [(x.call, x.pattern) for x in found.literals] == [
        ("Regex.IsMatch", "a+b"),
        ("Regex", "c"),
    ]
    assert found.composed == ()


def test_local_constant_is_not_followed_across_two_steps() -> None:
    found = scan('var p = "a";\nvar q = p;\nRegex.IsMatch(s, q);')
    assert found.literals == ()
    assert len(found.composed) == 1


@pytest.mark.parametrize(
    "body",
    [
        'var p = "a";\np = "b";\nRegex.IsMatch(s, p);',
        'var p = "a";\np += "b";\nRegex.IsMatch(s, p);',
        'string p;\nif (c) { p = "a"; }\nRegex.IsMatch(s, p);',
        'if (c) { var p = "a"; Regex.IsMatch(s, p); }',
        "foreach (var p in list) { Regex.IsMatch(s, p); }",
        'var p = "a";\nFoo(ref p);\nRegex.IsMatch(s, p);',
        "Foo(out var p);\nRegex.IsMatch(s, p);",
        'Regex.IsMatch(s, p);\nvar p = "a";',
    ],
)
def test_local_constant_is_not_followed(body: str) -> None:
    found = scan(body)
    assert found.literals == ()
    assert len(found.composed) == 1


def test_parameter_is_not_followed() -> None:
    source = b"class A { void F(string p) { Regex.IsMatch(s, p); } }"
    tree = parse(ADAPTER, source)
    found = ADAPTER.regex_literals(tree.root_node, source, "A.cs")
    assert found.literals == ()
    assert len(found.composed) == 1


def test_parenthesized_constant_is_literal() -> None:
    assert patterns('new Regex(("a" + ("b")));') == [("Regex", "ab")]


def test_non_plus_binary_expression_is_composed() -> None:
    result = scan('new Regex("a" - "b");\nnew Regex("a" == "b");')
    assert result.literals == ()
    assert [(c.call, c.line) for c in result.composed] == [("Regex", 2), ("Regex", 3)]


def test_other_attributes_are_not_picked() -> None:
    source = b'partial class A { [Obsolete("a+")] private static partial Regex R(); }'
    tree = parse(ADAPTER, source)
    found = ADAPTER.regex_literals(tree.root_node, source, "A.cs")
    assert found.literals == ()
    assert found.composed == ()


def generated_regex(attribute: str) -> RegexScan:
    source = f"partial class A {{ [{attribute}] private static partial Regex R(); }}".encode()
    tree = parse(ADAPTER, source)
    return ADAPTER.regex_literals(tree.root_node, source, "A.cs")


def test_generated_regex_without_arguments_is_not_picked() -> None:
    found = generated_regex("GeneratedRegex")
    assert found.literals == ()
    assert found.composed == ()


def test_generated_regex_named_pattern_argument() -> None:
    found = generated_regex('GeneratedRegex(pattern: "a")')
    assert [(x.call, x.pattern) for x in found.literals] == [("GeneratedRegex", "a")]
    assert found.composed == ()


def test_generated_regex_named_property_argument_is_composed() -> None:
    found = generated_regex("GeneratedRegex(Options = RegexOptions.None)")
    assert found.literals == ()
    assert [x.call for x in found.composed] == ["GeneratedRegex"]


@pytest.mark.parametrize("argument", ["ref p", "out var p", "p: ref q"])
def test_ref_argument_is_composed(argument: str) -> None:
    result = scan(f"new Regex({argument});")
    assert result.literals == ()
    assert len(result.composed) == 1


def test_local_value_of_a_non_identifier_is_none() -> None:
    source = b'class A { void F() { new Regex("a"); } }'
    tree = parse(ADAPTER, source)
    stack = [tree.root_node]
    literal = None
    while stack:
        node = stack.pop()
        if node.type == "string_literal":
            literal = node
        stack.extend(node.children)
    assert literal is not None
    assert local_value(literal, _LOCALS) is None
