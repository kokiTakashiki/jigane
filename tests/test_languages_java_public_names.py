from collections.abc import Collection, Mapping

import pytest
from tree_sitter import Node

from jigane.languages.base import PublicName, PublicNameKind, PublicNameReader, parse
from jigane.languages.java import JavaAdapter
from jigane.languages.java_public_names import added_public_names_of

_ADAPTER = JavaAdapter()


def _added(
    before: Mapping[str, str], after: Mapping[str, str], changed: Collection[str]
) -> list[PublicName]:
    return added_public_names_of(
        lambda source: parse(_ADAPTER, source.encode()).root_node, before, after, changed
    )


def _names(
    before: dict[str, str], after: dict[str, str], changed: set[str] | None = None
) -> dict[str, PublicName]:
    found = _added(before, after, set(after) if changed is None else changed)
    return {n.name: n for n in found}


def _one(source: str) -> dict[str, PublicName]:
    return _names({}, {"A.java": source})


def test_java_adapter_is_a_public_name_reader() -> None:
    assert isinstance(_ADAPTER, PublicNameReader)


def test_each_definition_kind_is_added() -> None:
    found = _one(
        "public class Shop {\n"
        "  static final int LIMIT = 1;\n"
        "  int count;\n"
        "  Shop(int n) {}\n"
        "  public void open() {}\n"
        "  enum Color { RED, GREEN; }\n"
        "}\n"
        "interface Api { void call(); int MAX = 3; }\n"
        "record Point(int x) { Point {} }\n"
        "@interface Mark { int value(); }\n"
    )
    kinds = {name: n.kind for name, n in found.items()}
    assert kinds == {
        "Shop": PublicNameKind.CLASS,
        "Shop.LIMIT": PublicNameKind.ENUM_VALUE,
        "Shop.Shop": PublicNameKind.METHOD,
        "Shop.open": PublicNameKind.METHOD,
        "Shop.Color": PublicNameKind.CLASS,
        "Shop.Color.RED": PublicNameKind.ENUM_VALUE,
        "Shop.Color.GREEN": PublicNameKind.ENUM_VALUE,
        "Api": PublicNameKind.CLASS,
        "Api.call": PublicNameKind.METHOD,
        "Api.MAX": PublicNameKind.ENUM_VALUE,
        "Point": PublicNameKind.CLASS,
        "Point.Point": PublicNameKind.METHOD,
        "Mark": PublicNameKind.CLASS,
    }
    assert (found["Shop.open"].path, found["Shop.open"].line) == ("A.java", 5)


def test_private_names_and_names_inside_a_private_type_are_left_out() -> None:
    found = _one(
        "class A {\n"
        "  private void hidden() {}\n"
        "  private static final int SECRET = 1;\n"
        "  private class Inner { public void deep() {} }\n"
        "  protected void shown() {}\n"
        "}\n"
    )
    assert set(found) == {"A", "A.shown"}


def test_interface_members_are_public_without_a_modifier() -> None:
    found = _one("interface I { void f(); private void g() {} }\n")
    assert set(found) == {"I", "I.f"}


def test_overloads_and_files_merge_into_one_name() -> None:
    after = {
        "A.java": "class A { void f() {} void f(int x) {} }\n",
        "B.java": "class B { void run(A a) { a.f(); } }\n",
    }
    found = _added({}, after, {"A.java", "B.java"})
    assert [n.name for n in found].count("A.f") == 1
    assert _names({}, after)["A.f"].callers == ("B.java の B.run",)


def test_a_name_present_before_is_not_added_even_if_it_became_public() -> None:
    before = {"A.java": "class A { private void f() {} }\n"}
    after = {"A.java": "class A { public void f() {} void g() {} }\n"}
    assert set(_names(before, after)) == {"A.g"}


def test_names_come_from_changed_files_only() -> None:
    after = {"A.java": "class A {}\n", "B.java": "class B { void m() { new A(); } }\n"}
    found = _names({}, after, {"B.java"})
    assert set(found) == {"B", "B.m"}


def test_declarations_in_a_method_body_are_not_definitions() -> None:
    found = _one(
        "class A { void m() {\n"
        "  class Local { void x() {} }\n"
        "  Runnable r = new Runnable() { public void run() {} };\n"
        "  Runnable q = () -> { class L2 {} };\n"
        "} }\n"
    )
    assert set(found) == {"A", "A.m"}


def test_method_calls_are_counted_by_name() -> None:
    after = {
        "A.java": "class A { void send() {} }\n",
        "B.java": "class B { void run(A a) { a.send(); } static void s() { new A().send(); } }\n",
    }
    send = _names({}, after)["A.send"]
    assert send.callers == ("B.java の B.run", "B.java の B.s")


def test_constructor_is_called_by_creation_but_not_super_or_this() -> None:
    after = {
        "A.java": "class A { A() {} A(int x) { this(); } }\n",
        "B.java": "class B extends A { B() { super(); } void m() { new A(1); } }\n",
    }
    found = _names({}, after)
    assert found["A.A"].callers == ("B.java の B.m",)


def test_class_is_counted_by_name_inheritance_and_creation() -> None:
    after = {
        "A.java": "interface Api {}\nclass Base {}\n",
        "B.java": (
            "class B extends Base implements Api {\n"
            "  Api field;\n"
            "  void m() { Object o = new Base(); }\n"
            "}\n"
        ),
    }
    found = _names({}, after)
    assert found["Api"].callers == ("B.java の B",)
    assert found["Base"].callers == ("B.java の B", "B.java の B.m")


def test_constant_is_counted_by_type_dot_value_only() -> None:
    after = {
        "A.java": "enum Color { RED, BLUE }\n",
        "B.java": (
            "class B {\n"
            "  void m(Color c) {\n"
            "    Color x = Color.RED;\n"
            "    switch (c) { case BLUE: break; default: break; }\n"
            "  }\n"
            "}\n"
        ),
    }
    found = _names({}, after)
    assert found["Color.RED"].callers == ("B.java の B.m",)
    assert found["Color.BLUE"].callers == ()


def test_recursion_is_not_a_caller() -> None:
    found = _one(
        "class A { A() { new A(); } void loop() { loop(); } A make() { return new A(); } }\n"
    )
    assert found["A.loop"].callers == ()
    assert found["A"].callers == ()
    assert found["A.A"].callers == ("A.java の A.make",)


def test_method_reference_is_not_a_caller() -> None:
    after = {
        "A.java": "class A { static void go() {} }\n",
        "B.java": "class B { Runnable r = A::go; }\n",
    }
    assert _names({}, after)["A.go"].callers == ()


@pytest.mark.parametrize("error", [RecursionError, MemoryError, ValueError])
def test_file_whose_tree_cannot_be_built_is_dropped(error: type[Exception]) -> None:
    def parse_source(source: str) -> Node:
        if "Deep" in source:
            raise error
        return parse(_ADAPTER, source.encode()).root_node

    found = added_public_names_of(
        parse_source,
        {},
        {"Deep.java": "class Deep { void f() {} }\n", "A.java": "class A {}\n"},
        {"Deep.java", "A.java"},
    )
    assert [n.name for n in found] == ["A"]


def test_same_name_of_different_kinds_stays_separate() -> None:
    after = {
        "A.java": (
            "class Outer { class Inner {} void Inner() {} static final int X = 1; void X() {} }\n"
        ),
        "B.java": "class B { void run(Outer o) { o.Inner(); } }\n",
    }
    found = _added({}, after, set(after))
    by_kind = {(n.name, n.kind): n for n in found}
    assert ("Outer.Inner", PublicNameKind.CLASS) in by_kind
    assert ("Outer.Inner", PublicNameKind.METHOD) in by_kind
    assert ("Outer.X", PublicNameKind.ENUM_VALUE) in by_kind
    assert ("Outer.X", PublicNameKind.METHOD) in by_kind
    assert len(found) == len({(n.name, n.kind) for n in found})
    assert by_kind[("Outer.Inner", PublicNameKind.METHOD)].callers == ("B.java の B.run",)
    assert (
        by_kind[("Outer.Inner", PublicNameKind.CLASS)].callers
        != by_kind[("Outer.Inner", PublicNameKind.METHOD)].callers
    )
