from fnmatch import fnmatchcase
from pathlib import Path

from jigane.judgment_map.configuration import DEFAULT_EXCLUDE
from jigane.languages.base import Declaration, Span, Visibility, parse
from jigane.languages.java import JavaAdapter
from jigane.languages.registry import adapter_for, is_build_path, is_test_path

adapter = JavaAdapter()


def declarations(code: str) -> dict[str, Declaration]:
    source = code.encode()
    found = adapter.declarations(parse(adapter, source).root_node, source)
    return {declaration.qualified_name: declaration for declaration in found}


def test_public_protected_package_private_and_private() -> None:
    found = declarations(
        "public class A {\n"
        "    public void a() {}\n"
        "    protected void b() {}\n"
        "    void c() {}\n"
        "    private void d() {}\n"
        "}\n"
    )
    assert found["A"].is_public
    assert found["A.a"].is_public
    assert found["A.b"].is_public
    assert found["A.c"].is_public
    assert not found["A.d"].is_public


def test_members_of_private_nested_type_are_not_public() -> None:
    found = declarations(
        "public class A {\n    private class B {\n        public void m() {}\n    }\n}\n"
    )
    assert not found["A.B"].is_public
    assert not found["A.B.m"].is_public


def test_interface_members_are_public_without_modifier() -> None:
    found = declarations(
        "interface I {\n"
        "    int K = 1;\n"
        "    void f();\n"
        "    default void g() {}\n"
        "    private void h() {}\n"
        "    class N {}\n"
        "}\n"
    )
    assert found["I.K"].is_public
    assert found["I.f"].is_public
    assert found["I.g"].is_public
    assert not found["I.h"].is_public
    assert found["I.N"].is_public


def test_kinds() -> None:
    found = declarations(
        "public class A {\n"
        "    int x;\n"
        "    A() {}\n"
        "    void m() {}\n"
        "    enum E { P, Q }\n"
        "    interface I {}\n"
        "    record R(int a) { R {} }\n"
        "    @interface N { int v(); }\n"
        "}\n"
    )
    assert found["A"].kind == "class"
    assert found["A.x"].kind == "variable"
    assert found["A.A"].kind == "method"
    assert found["A.m"].kind == "method"
    assert found["A.E"].kind == "class"
    assert found["A.E.P"].kind == "variable"
    assert found["A.I"].kind == "class"
    assert found["A.R"].kind == "class"
    assert found["A.R.R"].kind == "method"
    assert found["A.N"].kind == "class"
    assert found["A.N.v"].kind == "method"


def test_enum_members_after_semicolon_and_constant_bodies() -> None:
    found = declarations(
        "enum E {\n    P,\n    Q(1) {\n        void x() {}\n    };\n"
        "    E() {}\n    void e() {}\n}\n"
    )
    assert set(found) == {"E", "E.P", "E.Q", "E.E", "E.e"}


def test_qualified_names_of_nested_types_and_constructors() -> None:
    found = declarations("class A {\n    A() {}\n    class B {\n        void m() {}\n    }\n}\n")
    assert set(found) == {"A", "A.A", "A.B", "A.B.m"}


def test_overloads_share_qualified_name() -> None:
    source = b"class A {\n    void f(int a) {}\n    void f(String a) {}\n}\n"
    found = adapter.declarations(parse(adapter, source).root_node, source)
    assert [d.qualified_name for d in found].count("A.f") == 2


def test_only_anonymous_class_methods_are_nested_declarations() -> None:
    found = declarations(
        "class A {\n"
        "    void m() {\n"
        "        Runnable r = () -> {};\n"
        "        new Object() { void q() {} };\n"
        "        class Local { void l() {} }\n"
        "    }\n"
        "}\n"
    )
    assert set(found) == {"A", "A.m", "A.m.<anonymous1>.q"}


def test_multiline_signature_with_annotation() -> None:
    code = (
        "class A {\n"
        "    @Override\n"
        '    @SuppressWarnings("x")\n'
        "    public <T> void f(\n"
        "        T a,\n"
        "        int b) throws E {\n"
        "        int x = 1;\n"
        "        return;\n"
        "    }\n"
        "}\n"
    )
    found = declarations(code)["A.f"]
    assert found.span == Span(2, 9)
    assert found.signature == Span(2, 6)
    assert found.body == Span(7, 9)


def test_one_line_method_has_no_body() -> None:
    found = declarations("class A {\n    int f() { return 1; }\n}\n")["A.f"]
    assert found.signature == Span(2, 2)
    assert found.body is None


def test_abstract_method_is_all_signature() -> None:
    found = declarations("abstract class A {\n    abstract void f(\n        int a);\n}\n")
    assert found["A.f"].signature == Span(2, 3)
    assert found["A.f"].body is None


def test_class_signature_and_body() -> None:
    found = declarations("public class A\n    extends B {\n    int x;\n}\n")["A"]
    assert found.signature == Span(1, 2)
    assert found.body == Span(3, 4)


def test_fields_are_separate_declarations_with_whole_signature() -> None:
    found = declarations(
        "class A {\n    @Deprecated\n    public static final int X = 1, Y = 2;\n}\n"
    )
    for name in ("A.X", "A.Y"):
        assert found[name].kind == "variable"
        assert found[name].is_public
        assert found[name].span == Span(2, 3)
        assert found[name].signature == Span(2, 3)
        assert found[name].body is None


def test_conventions() -> None:
    assert isinstance(adapter_for("src/main/java/A.java"), JavaAdapter)
    assert adapter.comment_node_types == {"line_comment", "block_comment"}
    adapters = [adapter]
    for path in (
        "src/test/java/a/B.java",
        "a/FooTest.java",
        "a/FooTests.java",
        "a/FooIT.java",
    ):
        assert is_test_path(path, adapters)
    assert not is_test_path("src/main/java/Foo.java", adapters)
    for path in (
        "pom.xml",
        "app/build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "gradle.properties",
        "gradle/libs.versions.toml",
    ):
        assert is_build_path(path, adapters)


def test_default_exclude_has_gradle_lockfile() -> None:
    for path in ("gradle.lockfile", "app/gradle.lockfile"):
        assert any(fnmatchcase(path, pattern) for pattern in DEFAULT_EXCLUDE)


def test_parses_a_realistic_java_file() -> None:
    data = Path(__file__).parent.joinpath("fixtures", "Inventory.java").read_bytes()
    line_count = data.count(b"\n") + 1
    found = adapter.declarations(parse(adapter, data).root_node, data)
    assert len(found) > 40
    for declaration in found:
        assert 1 <= declaration.span.start_line <= declaration.span.end_line <= line_count
        assert declaration.span.start_line == declaration.signature.start_line


def test_java_visibility_levels() -> None:
    found = declarations(
        "public class A {\n"
        "    public void a() {}\n"
        "    protected void b() {}\n"
        "    void c() {}\n"
        "    private void d() {}\n"
        "}\n"
        "interface I { void m(); }\n"
    )
    assert found["A"].visibility is Visibility.PUBLIC
    assert found["A.a"].visibility is Visibility.PUBLIC
    assert found["A.b"].visibility is Visibility.PUBLIC
    assert found["A.c"].visibility is Visibility.INTERNAL
    assert found["A.d"].visibility is Visibility.FILE
    assert found["I"].visibility is Visibility.INTERNAL
    assert found["I.m"].visibility is Visibility.INTERNAL


def test_public_method_of_package_private_class_is_internal() -> None:
    found = declarations("class A { public void m() {} private void n() {} }\n")
    assert found["A"].visibility is Visibility.INTERNAL
    assert found["A.m"].visibility is Visibility.INTERNAL
    assert found["A.n"].visibility is Visibility.FILE
