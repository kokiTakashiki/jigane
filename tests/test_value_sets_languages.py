import pytest

from jigane.languages.base import (
    ComparisonKind,
    DefinitionKind,
    LanguageAdapter,
    ValueSet,
    ValueSetReader,
)
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.java import JavaAdapter
from jigane.languages.python import PythonAdapter
from jigane.languages.swift import SwiftAdapter
from jigane.languages.value_set_core import MAX_VALUE_LENGTH

LONG = "x" * (MAX_VALUE_LENGTH + 1)


def _found(adapter: ValueSetReader, source: str) -> list[tuple[int, set[str]]]:
    return [(v.line, set(v.values)) for v in adapter.value_sets(source, "m")]


def _names(adapter: ValueSetReader, source: str) -> list[str | None]:
    return [v.name for v in adapter.value_sets(source, "m")]


@pytest.mark.parametrize("adapter", [JavaAdapter(), CSharpAdapter(), SwiftAdapter()])
def test_adapter_satisfies_the_reader_protocol(adapter: LanguageAdapter) -> None:
    assert isinstance(adapter, ValueSetReader)


def test_java_collects_array_and_factory_literals() -> None:
    source = (
        "class A {\n"
        '  String[] a = new String[]{"a1", "b1", "c1"};\n'
        '  String[] b = {"a2", "b2", "c2"};\n'
        '  var c = List.of("a3", "b3", "c3");\n'
        '  var d = Set.of("a4", "b4", "c4");\n'
        '  var e = Arrays.asList("a5", "b5", "c5");\n'
        "}\n"
    )
    assert [line for line, _ in _found(JavaAdapter(), source)] == [2, 3, 4, 5, 6]


def test_java_skips_short_non_string_and_long_elements() -> None:
    source = (
        "class A {\n"
        '  var a = List.of("a", "b");\n'
        '  var b = List.of("a", "b", 3);\n'
        f'  var c = List.of("{LONG}", "b", "c");\n'
        '  var d = Map.of("a", "b", "c");\n'
        '  var e = List.of("a", "b", "c" + "d");\n'
        '  String f = "plain";\n'
        "}\n"
    )
    assert _found(JavaAdapter(), source) == []


def test_java_collects_enum_string_arguments() -> None:
    source = 'enum E {\n  X("x1"), Y("y1"), Z("z1");\n  E(String s) {}\n}\n'
    assert _found(JavaAdapter(), source) == [(1, {"x1", "y1", "z1"})]


def test_java_skips_enum_with_a_non_string_or_missing_argument() -> None:
    assert _found(JavaAdapter(), 'enum E { X("x1"), Y("y1"), Z }\n') == []
    assert _found(JavaAdapter(), 'enum E { X("x1"), Y("y1"), Z(3) }\n') == []
    assert _found(JavaAdapter(), 'enum E { X("x1"), Y("y1") }\n') == []


def test_java_names_come_from_declarations_and_enum_types() -> None:
    source = (
        "class A {\n"
        '  static final Set<String> NAMES = Set.of("a1", "b1", "c1");\n'
        '  String[] arr = new String[]{"a2", "b2", "c2"};\n'
        '  void f() { g(List.of("a3", "b3", "c3")); }\n'
        "}\n"
        'enum Kind { X("x1"), Y("y1"), Z("z1"); }\n'
    )
    assert _names(JavaAdapter(), source) == ["NAMES", "arr", None, "Kind"]


def test_csharp_collects_initializers_and_collection_expressions() -> None:
    source = (
        "class A {\n"
        '  string[] a = {"a1", "b1", "c1"};\n'
        '  var b = new[] {"a2", "b2", "c2"};\n'
        '  var c = new string[] {"a3", "b3", "c3"};\n'
        '  var d = new HashSet<string> {"a4", "b4", "c4"};\n'
        '  string[] e = ["a5", "b5", "c5"];\n'
        "}\n"
    )
    assert [line for line, _ in _found(CSharpAdapter(), source)] == [2, 3, 4, 5, 6]


def test_csharp_skips_short_non_string_and_long_elements() -> None:
    source = (
        "class A {\n"
        '  var a = new[] {"a", "b"};\n'
        '  var b = new[] {"a", "b", 3};\n'
        f'  var c = new[] {{"{LONG}", "b", "c"}};\n'
        '  string[] d = ["a", "b"];\n'
        '  var e = new X { A = "a", B = "b", C = "c" };\n'
        "}\n"
    )
    assert _found(CSharpAdapter(), source) == []


def test_csharp_collects_string_constants() -> None:
    source = (
        "static class K {\n"
        '  public const string A = "a1";\n'
        '  const string B = "b1";\n'
        '  const string C = "c1";\n'
        "}\n"
    )
    assert _found(CSharpAdapter(), source) == [(1, {"a1", "b1", "c1"})]


def test_csharp_names_come_from_declarations_and_type_names() -> None:
    source = (
        "class K {\n"
        '  string[] a = new[] {"a1", "b1", "c1"};\n'
        '  List<string> b = ["a2", "b2", "c2"];\n'
        '  void F() { G(new[] {"a3", "b3", "c3"}); }\n'
        "}\n"
        'static class Consts { const string A = "a"; const string B = "b";'
        ' const string C = "c"; }\n'
    )
    assert _names(CSharpAdapter(), source) == ["a", "b", None, "Consts"]


def test_csharp_skips_constants_mixed_with_other_members() -> None:
    mixed = 'class K { const string A = "a"; const string B = "b"; const int C = 3; }\n'
    with_field = 'class K { const string A = "a"; const string B = "b"; string C = "c"; }\n'
    two = 'class K { const string A = "a"; const string B = "b"; }\n'
    for source in (mixed, with_field, two):
        assert _found(CSharpAdapter(), source) == []


def test_swift_collects_array_and_set_literals() -> None:
    source = 'let a = ["a1", "b1", "c1"]\nlet b: Set<String> = ["a2", "b2", "c2"]\n'
    assert [line for line, _ in _found(SwiftAdapter(), source)] == [1, 2]


def test_swift_names_come_from_bindings_and_enum_types() -> None:
    source = (
        'let a = ["a1", "b1", "c1"]\n'
        'var b: [String] = ["a2", "b2", "c2"]\n'
        'f(["a3", "b3", "c3"])\n'
        'enum Kind: String { case a = "v1", b = "v2", c = "v3" }\n'
    )
    assert _names(SwiftAdapter(), source) == ["a", "b", None, "Kind"]


def test_swift_skips_short_non_string_and_long_elements() -> None:
    source = (
        'let a = ["a", "b"]\n'
        'let b = ["a", "b", 3]\n'
        f'let c = ["{LONG}", "b", "c"]\n'
        'let d = ["a": 1, "b": 2, "c": 3]\n'
        'let e = ["a", "b", "c\\(x)"]\n'
    )
    assert _found(SwiftAdapter(), source) == []


def test_swift_collects_string_enum_values() -> None:
    source = 'enum E: String {\n  case a = "v1", b = "v2"\n  case c = "v3"\n}\n'
    assert _found(SwiftAdapter(), source) == [(1, {"v1", "v2", "v3"})]


def test_swift_skips_enums_without_string_values() -> None:
    assert _found(SwiftAdapter(), 'enum E: String { case a = "v1", b, c = "v3" }\n') == []
    assert _found(SwiftAdapter(), 'enum E { case a = "v1", b = "v2", c = "v3" }\n') == []
    assert _found(SwiftAdapter(), 'enum E: String { case a = "v1", b = "v2" }\n') == []


def _comparisons(adapter: ValueSetReader, source: str) -> list[tuple[int, ComparisonKind, str]]:
    return [(c.line, c.kind, c.value) for c in adapter.string_comparisons(source, "m")]


def test_java_collects_the_four_comparison_shapes() -> None:
    source = (
        "class A {\n"
        '  static final List<String> NAMES = List.of("n1", "n2");\n'
        "  void f(String x) {\n"
        '    if (x.equals("e1") || "e2".equals(x)) {}\n'
        "    NAMES.contains(x);\n"
        '    Set.of("s1").contains(x);\n'
        '    x.startsWith("p1"); x.endsWith("q1");\n'
        '    Pattern.compile("r1"); x.matches("r2");\n'
        "  }\n"
        "}\n"
    )
    assert _comparisons(JavaAdapter(), source) == [
        (4, ComparisonKind.EQUALS, "e1"),
        (4, ComparisonKind.EQUALS, "e2"),
        (5, ComparisonKind.MEMBERSHIP, "n1"),
        (5, ComparisonKind.MEMBERSHIP, "n2"),
        (6, ComparisonKind.MEMBERSHIP, "s1"),
        (7, ComparisonKind.PREFIX, "p1"),
        (7, ComparisonKind.SUFFIX, "q1"),
        (8, ComparisonKind.REGEX, "r1"),
        (8, ComparisonKind.REGEX, "r2"),
    ]


def test_java_skips_non_constant_arguments_and_unresolved_names() -> None:
    source = (
        "class A {\n"
        "  void f(String x, String y, List<String> other) {\n"
        "    x.equals(y); x.startsWith(y); x.matches(y); Pattern.compile(y);\n"
        '    other.contains(x); x == "v"; x.startsWith("");\n'
        '    switch (x) { case "k": break; default: break; }\n'
        "  }\n"
        "}\n"
    )
    assert _comparisons(JavaAdapter(), source) == []


def test_csharp_collects_the_four_comparison_shapes() -> None:
    source = (
        "class A {\n"
        '  static readonly string[] Names = {"n1", "n2"};\n'
        "  void F(string x) {\n"
        '    if (x == "e1" || "e2" != x) {}\n'
        "    Names.Contains(x);\n"
        '    new[] {"s1"}.Contains(x);\n'
        '    x.StartsWith("p1"); x.EndsWith("q1");\n'
        '    Regex.IsMatch(x, "r1"); var r = new Regex("r2");\n'
        "  }\n"
        "}\n"
    )
    assert _comparisons(CSharpAdapter(), source) == [
        (4, ComparisonKind.EQUALS, "e1"),
        (4, ComparisonKind.EQUALS, "e2"),
        (5, ComparisonKind.MEMBERSHIP, "n1"),
        (5, ComparisonKind.MEMBERSHIP, "n2"),
        (6, ComparisonKind.MEMBERSHIP, "s1"),
        (7, ComparisonKind.PREFIX, "p1"),
        (7, ComparisonKind.SUFFIX, "q1"),
        (8, ComparisonKind.REGEX, "r1"),
        (8, ComparisonKind.REGEX, "r2"),
    ]


def test_csharp_skips_non_constant_arguments_and_unresolved_names() -> None:
    source = (
        "class A {\n"
        "  void F(string x, string y, string[] other) {\n"
        "    if (x == y) {}\n"
        "    x.StartsWith(y); Regex.IsMatch(x, y); new Regex(y);\n"
        '    other.Contains(x); x.StartsWith("");\n'
        '    switch (x) { case "k": break; }\n'
        "  }\n"
        "}\n"
    )
    assert _comparisons(CSharpAdapter(), source) == []


def test_swift_collects_the_four_comparison_shapes() -> None:
    source = (
        'let names = ["n1", "n2"]\n'
        "func f(x: String) {\n"
        '  if x == "e1" || "e2" != x {}\n'
        "  _ = names.contains(x)\n"
        '  _ = ["s1"].contains(x)\n'
        '  _ = x.hasPrefix("p1"); _ = x.hasSuffix("q1")\n'
        '  _ = try NSRegularExpression(pattern: "r1"); _ = try Regex("r2")\n'
        "}\n"
    )
    assert _comparisons(SwiftAdapter(), source) == [
        (3, ComparisonKind.EQUALS, "e1"),
        (3, ComparisonKind.EQUALS, "e2"),
        (4, ComparisonKind.MEMBERSHIP, "n1"),
        (4, ComparisonKind.MEMBERSHIP, "n2"),
        (5, ComparisonKind.MEMBERSHIP, "s1"),
        (6, ComparisonKind.PREFIX, "p1"),
        (6, ComparisonKind.SUFFIX, "q1"),
        (7, ComparisonKind.REGEX, "r1"),
        (7, ComparisonKind.REGEX, "r2"),
    ]


def test_swift_skips_non_constant_arguments_and_unresolved_names() -> None:
    source = (
        "func f(x: String, y: String, other: [String]) {\n"
        "  if x == y {}\n"
        "  _ = x.hasPrefix(y); _ = try Regex(y)\n"
        '  _ = other.contains(x); _ = x.hasPrefix("")\n'
        '  switch x { case "k": break; default: break }\n'
        "}\n"
    )
    assert _comparisons(SwiftAdapter(), source) == []


def _kinds(adapter: ValueSetReader, source: str) -> list[DefinitionKind]:
    return [v.kind for v in adapter.value_sets(source, "m")]


def test_python_marks_named_definitions_and_literals() -> None:
    source = 'STATUSES = ("a1", "b1", "c1")\nuse(("a2", "b2", "c2"))\n'
    assert _kinds(PythonAdapter(), source) == [DefinitionKind.NAMED, DefinitionKind.LITERAL]


def test_java_marks_named_definitions_and_literals() -> None:
    source = (
        "class A {\n"
        '  String[] a = {"a1", "b1", "c1"};\n'
        '  void f() { g(List.of("a2", "b2", "c2")); }\n'
        "}\n"
    )
    assert _kinds(JavaAdapter(), source) == [DefinitionKind.NAMED, DefinitionKind.LITERAL]


def test_csharp_marks_named_definitions_and_literals() -> None:
    source = (
        "class A {\n"
        '  string[] a = { "a1", "b1", "c1" };\n'
        '  void F() { G(new[] { "a2", "b2", "c2" }); }\n'
        "}\n"
    )
    assert _kinds(CSharpAdapter(), source) == [DefinitionKind.NAMED, DefinitionKind.LITERAL]


def test_swift_marks_named_definitions_and_literals() -> None:
    source = 'let a = ["a1", "b1", "c1"]\nuse(["a2", "b2", "c2"])\n'
    assert _kinds(SwiftAdapter(), source) == [DefinitionKind.NAMED, DefinitionKind.LITERAL]


def test_java_wrapped_initializer_is_undetermined() -> None:
    source = (
        "class A {\n"
        '  static final Set<String> S = new HashSet<>(Arrays.asList("d", "e", "f"));\n'
        "}\n"
    )
    assert _kinds(JavaAdapter(), source) == [DefinitionKind.UNDETERMINED]


def test_python_destructuring_assignment_is_undetermined() -> None:
    source = 'A, B = ("j", "k", "l"), 1\n'
    assert _kinds(PythonAdapter(), source) == [DefinitionKind.UNDETERMINED]


def test_python_dict_value_in_assignment_is_undetermined() -> None:
    source = 'd = {"k": ("g", "h", "i")}\n'
    assert _kinds(PythonAdapter(), source) == [DefinitionKind.UNDETERMINED]


def test_csharp_wrapped_initializer_without_name_is_undetermined() -> None:
    source = 'class A {\n  List<string> a = new List<string>(new[] { "a1", "b1", "c1" });\n}\n'
    assert _kinds(CSharpAdapter(), source) == [DefinitionKind.UNDETERMINED]


def test_swift_wrapped_initializer_without_name_is_undetermined() -> None:
    source = 'let a = Set(["a1", "b1", "c1"])\n'
    assert _kinds(SwiftAdapter(), source) == [DefinitionKind.UNDETERMINED]


@pytest.mark.parametrize("name", ["STATUSES", "x", "_q9", "日本語"])
def test_python_kind_does_not_depend_on_the_spelling_of_the_name(name: str) -> None:
    source = f'{name} = ("a1", "b1", "c1")\n'
    assert _kinds(PythonAdapter(), source) == [DefinitionKind.NAMED]


def test_value_set_kind_defaults_to_undetermined() -> None:
    assert ValueSet(frozenset({"a"}), "m", 1).kind == DefinitionKind.UNDETERMINED


# 十分に深い式。600 項は parse が通っても後段の再帰で落ち、20,000 項は parse 自身が落ちる
_DEEP_SOURCES = {
    "comparison_600": 'x = "a" == ' + "+".join(["a"] * 600) + "\n",
    "comparison_20000": 'x = "a" == ' + "+".join(["a"] * 20_000) + "\n",
    "sum_20000": "x = " + "+".join(["a"] * 20_000) + "\n",
}


@pytest.mark.parametrize("source", _DEEP_SOURCES.values(), ids=_DEEP_SOURCES.keys())
def test_python_string_comparisons_skip_a_file_with_a_deep_expression(source: str) -> None:
    assert PythonAdapter().string_comparisons(source, "deep.py") == []


@pytest.mark.parametrize("source", _DEEP_SOURCES.values(), ids=_DEEP_SOURCES.keys())
def test_python_value_sets_skip_a_file_with_a_deep_expression(source: str) -> None:
    assert PythonAdapter().value_sets(source, "deep.py") == []
