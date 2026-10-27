from collections.abc import Iterator
from fnmatch import fnmatchcase

import pytest
from tree_sitter import Node

from jigane.judgment_map.configuration import DEFAULT_EXCLUDE
from jigane.languages import registry
from jigane.languages.base import (
    Declaration,
    MemberDecl,
    SequenceAdapter,
    Span,
    TypeDecl,
    Visibility,
    parse,
)
from jigane.languages.csharp import CSharpAdapter

adapter = CSharpAdapter()


def declarations(code: str) -> dict[str, Declaration]:
    source = code.encode()
    found = adapter.declarations(parse(adapter, source).root_node, source)
    return {declaration.qualified_name: declaration for declaration in found}


@pytest.mark.parametrize(
    ("modifiers", "expected"),
    [
        ("public", True),
        ("protected", True),
        ("internal", True),
        ("protected internal", True),
        ("private", False),
        ("private protected", False),
        ("", False),
        ("static", False),
        ("public static", True),
    ],
)
def test_class_member_visibility(modifiers: str, *, expected: bool) -> None:
    found = declarations(f"public class C {{ {modifiers} void M() {{ }} }}")
    assert found["C.M"].is_public is expected


def test_struct_and_record_members_default_to_private() -> None:
    found = declarations("public struct S { void M() { } }\npublic record R { void M() { } }\n")
    assert not found["S.M"].is_public
    assert not found["R.M"].is_public


def test_top_level_type_without_modifier_is_public() -> None:
    found = declarations("class C { public void M() { } }")
    assert found["C"].is_public
    assert found["C.M"].is_public


def test_private_top_level_type_hides_members() -> None:
    found = declarations("file class C { public void M() { } }")
    assert not found["C"].is_public
    assert not found["C.M"].is_public


def test_private_nested_type_hides_members() -> None:
    found = declarations(
        "public class O { private class N { public void M() { } }"
        " class P { public void M() { } }"
        " public class Q { public void M() { } } }"
    )
    assert not found["O.N.M"].is_public
    assert not found["O.P"].is_public
    assert not found["O.P.M"].is_public
    assert found["O.Q.M"].is_public


def test_interface_and_enum_members_are_public() -> None:
    found = declarations("interface I { void M(); int P { get; } }\nenum E { A, B = 2 }\n")
    assert found["I.M"].is_public
    assert found["I.P"].is_public
    assert found["E.A"].is_public
    assert found["E.B"].is_public


def test_namespace_is_not_in_qualified_name() -> None:
    block = declarations("namespace A.B { public class C { public void M() { } } }")
    scoped = declarations("namespace A.B;\npublic class C { public void M() { } }\n")
    assert list(block) == ["C", "C.M"]
    assert list(scoped) == ["C", "C.M"]
    assert block["C"].kind == "class"


def test_nested_qualified_names() -> None:
    found = declarations("public class A { public class B { public enum E { X } public int F; } }")
    assert set(found) == {"A", "A.B", "A.B.E", "A.B.E.X", "A.B.F"}
    assert found["A.B.F"].kind == "variable"


def test_constructor_uses_type_name_and_overloads_share_name() -> None:
    found = declarations(
        "public class C {\n"
        "    public C() { }\n"
        "    public void M(int a) { }\n"
        "    public void M(string a) { }\n"
        "}\n"
    )
    assert found["C.C"].kind == "method"
    assert "C.M" in found
    source = b"public class C { void M(int a) { } void M(string a) { } }"
    raw = adapter.declarations(parse(adapter, source).root_node, source)
    assert [d.qualified_name for d in raw] == ["C", "C.M", "C.M"]


def test_kinds() -> None:
    found = declarations(
        "public class C {\n"
        "    public int a, b;\n"
        "    public int P { get; set; }\n"
        "    public event System.Action E;\n"
        "    public int this[int i] { get { return 1; } }\n"
        "    public delegate void D(int x);\n"
        "}\n"
    )
    assert found["C.a"].kind == "variable"
    assert found["C.b"].kind == "variable"
    assert found["C.P"].kind == "variable"
    assert found["C.E"].kind == "variable"
    assert found["C.this[]"].kind == "method"
    assert found["C.D"].kind == "class"


def test_lambda_and_local_function_are_not_declarations() -> None:
    found = declarations(
        "public class C { public void M() {\n"
        "    int L() => 1;\n"
        "    System.Func<int> f = () => 2;\n"
        "} }"
    )
    assert set(found) == {"C", "C.M"}


def test_top_level_statements_are_ignored() -> None:
    found = declarations("System.Console.WriteLine(1);\npublic class C { }\n")
    assert list(found) == ["C"]


def test_declarations_inside_preprocessor_branches_are_found() -> None:
    found = declarations(
        "public class C {\n#if DEBUG\n    public void A() { }\n#else\n"
        "    public void B() { }\n#endif\n}\n"
    )
    assert {"C.A", "C.B"} <= set(found)


def test_multiline_signature_and_body() -> None:
    code = (
        "public class C {\n"  # 1
        "    public int M(\n"  # 2
        "        int a,\n"  # 3
        "        int b)\n"  # 4
        "    {\n"  # 5
        "        return a;\n"  # 6
        "    }\n"  # 7
        "}\n"
    )
    found = declarations(code)["C.M"]
    assert found.span == Span(2, 7)
    assert found.signature == Span(2, 5)
    assert found.body == Span(6, 7)


def test_attribute_is_part_of_signature_and_span() -> None:
    code = (
        "public class C {\n"  # 1
        "    [Obsolete]\n"  # 2
        "    [Foo(1)]\n"  # 3
        "    public void M() {\n"  # 4
        "        x();\n"  # 5
        "    }\n"  # 6
        "}\n"
    )
    found = declarations(code)["C.M"]
    assert found.span == Span(2, 6)
    assert found.signature == Span(2, 4)
    assert found.body == Span(5, 6)


def test_one_line_method_has_no_body() -> None:
    found = declarations("public class C { public void M() { } }")["C.M"]
    assert found.body is None
    assert found.signature == Span(1, 1)


def test_expression_bodied_method_and_property() -> None:
    code = (
        "public class C {\n"  # 1
        "    public int M() => 1;\n"  # 2
        "    public int P =>\n"  # 3
        "        compute(\n"  # 4
        "            1);\n"  # 5
        "}\n"
    )
    found = declarations(code)
    assert found["C.M"].signature == Span(2, 2)
    assert found["C.M"].body is None
    assert found["C.P"].signature == Span(3, 3)
    assert found["C.P"].body == Span(4, 5)


def test_auto_property_is_signature_only() -> None:
    code = (
        "public class C {\n"  # 1
        "    public int P {\n"  # 2
        "        get;\n"  # 3
        "        set;\n"  # 4
        "    }\n"  # 5
        "}\n"
    )
    found = declarations(code)["C.P"]
    assert found.span == Span(2, 5)
    assert found.signature == Span(2, 5)
    assert found.body is None


def test_property_with_accessor_bodies() -> None:
    code = (
        "public class C {\n"  # 1
        "    public int P {\n"  # 2
        "        get { return 1; }\n"  # 3
        "    }\n"  # 4
        "}\n"
    )
    found = declarations(code)["C.P"]
    assert found.signature == Span(2, 2)
    assert found.body == Span(3, 4)


def test_abstract_method_and_interface_method_are_signature_only() -> None:
    found = declarations(
        "public abstract class C {\n    public abstract void M();\n}\n"
        "public interface I {\n    void N();\n}\n"
    )
    assert found["C.M"].signature == Span(2, 2)
    assert found["C.M"].body is None
    assert found["I.N"].body is None


def test_fields_share_a_span() -> None:
    code = "public class C {\n    public int a,\n        b;\n}\n"
    found = declarations(code)
    assert found["C.a"].span == Span(2, 3)
    assert found["C.b"].signature == Span(2, 3)
    assert found["C.b"].body is None


def test_class_body_starts_after_opening_line() -> None:
    code = "public class C\n{\n    public int a;\n}\n"
    found = declarations(code)["C"]
    assert found.signature == Span(1, 2)
    assert found.body == Span(3, 4)


def test_suffix_and_registry() -> None:
    assert isinstance(registry.adapter_for("src/Foo.cs"), CSharpAdapter)
    assert isinstance(registry.adapter_for("Foo.CS"), CSharpAdapter)


def test_test_paths() -> None:
    adapters = [adapter]
    for path in [
        "Foo.Tests/A.cs",
        "Foo.UnitTests/A.cs",
        "Foo.IntegrationTests/sub/A.cs",
        "FooTests/A.cs",
        "src/FooTests.cs",
        "src/FooTest.cs",
    ]:
        assert registry.is_test_path(path, adapters), path
    assert not registry.is_test_path("src/Foo.cs", adapters)


def test_build_paths() -> None:
    adapters = [adapter]
    for path in [
        "src/Foo.csproj",
        "Foo.sln",
        "Foo.slnx",
        "Directory.Build.props",
        "a/Directory.Build.targets",
        "Directory.Packages.props",
        "NuGet.config",
        "nuget.config",
        "global.json",
    ]:
        assert registry.is_build_path(path, adapters), path
    assert not registry.is_build_path("src/Foo.cs", adapters)


def test_generated_defaults() -> None:
    for path in [
        "packages.lock.json",
        "src/packages.lock.json",
        "Form1.Designer.cs",
        "src/obj/Foo.g.cs",
    ]:
        assert any(fnmatchcase(path, g) for g in DEFAULT_EXCLUDE), path


def test_enum_has_body_span_like_class() -> None:
    found = declarations("enum E\n{\n    A,\n    B\n}\nclass C\n{\n    int F;\n}\n")
    assert found["C"].body == Span(8, 9)
    assert found["E"].body == Span(3, 5)


@pytest.mark.parametrize(
    ("modifiers", "expected"),
    [
        ("public", Visibility.PUBLIC),
        ("protected", Visibility.PUBLIC),
        ("protected internal", Visibility.PUBLIC),
        ("internal", Visibility.INTERNAL),
        ("private", Visibility.FILE),
        ("private protected", Visibility.FILE),
        ("", Visibility.FILE),
    ],
)
def test_csharp_member_visibility_levels(modifiers: str, expected: Visibility) -> None:
    found = declarations(f"public class C {{ {modifiers} void M() {{ }} }}")
    assert found["C.M"].visibility is expected


def test_top_level_type_without_modifier_is_internal() -> None:
    found = declarations("class C { public void M() { } }\nfile class F { public void M() { } }\n")
    assert found["C"].visibility is Visibility.INTERNAL
    assert found["C.M"].visibility is Visibility.INTERNAL
    assert found["F"].visibility is Visibility.FILE
    assert found["F.M"].visibility is Visibility.FILE


def test_public_method_of_internal_class_is_internal() -> None:
    found = declarations("internal class C { public void M() { } }")
    assert found["C.M"].visibility is Visibility.INTERNAL


def test_interface_and_enum_members_take_container_level() -> None:
    found = declarations("interface I { void M(); }\npublic enum E { A }\n")
    assert found["I.M"].visibility is Visibility.INTERNAL
    assert found["E.A"].visibility is Visibility.PUBLIC


def _type_members(source: str) -> tuple[list[TypeDecl], list[MemberDecl]]:
    code = source.encode()
    decls = adapter.type_members(parse(adapter, code).root_node, code, "A.cs")
    types = [d for d in decls if isinstance(d, TypeDecl)]
    members = [d for d in decls if isinstance(d, MemberDecl)]
    return types, members


def test_type_members_lists_types_bases_and_members_in_order() -> None:
    types, members = _type_members(
        "namespace N { public partial class A : B, I<int> {\n"
        "  int f, g;\n"
        "  public string P { get; set; }\n"
        "  public A(int x, string y = null) {}\n"
        "  void M(int a) {}\n"
        "  void M(int a, int b) {}\n"
        "  class Inner { void Z() {} }\n"
        "} }\n"
    )
    assert [(t.qualified_name, t.kind, t.bases, t.line) for t in types] == [
        ("A", "class", ("B", "I<int>"), 1),
        ("A.Inner", "class", (), 7),
    ]
    assert [(m.owner, m.name, m.kind, m.type_annotation, m.line) for m in members] == [
        ("A", "f", "field", "int", 2),
        ("A", "g", "field", "int", 2),
        ("A", "P", "property", "string", 3),
        ("A", "A", "constructor", None, 4),
        ("A", "M", "method", "void", 5),
        ("A", "M", "method", "void", 6),
        ("A.Inner", "Z", "method", "void", 7),
    ]
    constructor = members[3]
    assert [(p.name, p.type_annotation, p.optional) for p in constructor.parameters] == [
        ("x", "int", False),
        ("y", "string", True),
    ]
    assert constructor.body is not None
    assert members[2].body is None
    assert [len(m.parameters) for m in members[4:6]] == [1, 2]


def test_type_members_returns_partial_declarations_unmerged() -> None:
    types, _ = _type_members("partial class A {}\npartial class A : B {}\n")
    assert [(t.qualified_name, t.bases) for t in types] == [("A", ()), ("A", ("B",))]


def test_type_members_emits_primary_constructor_parameters() -> None:
    types, members = _type_members("record R(int X, string Y) { }\n")
    assert [t.kind for t in types] == ["record"]
    assert [(m.name, m.kind, m.type_annotation, m.line) for m in members] == [
        ("X", "parameter", "int", 1),
        ("Y", "parameter", "string", 1),
    ]


def test_csharp_adapter_implements_sequence_adapter() -> None:
    assert isinstance(adapter, SequenceAdapter)
    assert isinstance(registry.adapter_for("a.py"), SequenceAdapter)


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


@pytest.mark.parametrize(
    ("lambda_text", "expected"),
    [
        ("p => p.Name", {"p"}),
        ("(p) => p.Name", {"p"}),
        ("(a, b) => a", {"a", "b"}),
        ("(Person p) => p.Name", {"p"}),
        ("() => 1", set()),
    ],
)
def test_lambda_parameters_are_locals(lambda_text: str, expected: set[str]) -> None:
    source = f"class C {{ void M() {{ var f = {lambda_text}; }} }}".encode()
    body = next(n for n in _walk(parse(adapter, source).root_node) if n.type == "block")
    assert adapter.locals_of(body).lambda_parameters == expected
