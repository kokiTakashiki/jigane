from jigane.languages.base import PublicName, PublicNameKind, PublicNameReader
from jigane.languages.csharp import CSharpAdapter

_ADAPTER = CSharpAdapter()


def _names(
    before: dict[str, str], after: dict[str, str], changed: set[str] | None = None
) -> dict[str, PublicName]:
    found = _ADAPTER.added_public_names(before, after, set(after) if changed is None else changed)
    return {n.name: n for n in found}


def _added(source: str, others: dict[str, str] | None = None) -> dict[str, PublicName]:
    return _names({}, {"A.cs": source, **(others or {})}, {"A.cs"})


def test_csharp_adapter_is_a_public_name_reader() -> None:
    assert isinstance(_ADAPTER, PublicNameReader)


def test_each_kind_is_defined_with_a_qualified_name() -> None:
    source = (
        "namespace N {\n"
        "public class C {\n"
        "  public C() {}\n"
        "  public void M() {}\n"
        "  public const int K = 1;\n"
        "  public int P { get; set; }\n"
        "  public int F;\n"
        "  public event System.Action E;\n"
        "  public static C operator +(C a, C b) => a;\n"
        "  public int this[int i] => i;\n"
        "  public class Inner { public void Go() {} }\n"
        "}\n"
        "public struct S {}\n"
        "public record R(int X);\n"
        "public interface I { void Do(); }\n"
        "public enum E { X, Y }\n"
        "}\n"
    )
    found = _added(source)
    kinds = {name: n.kind for name, n in found.items()}
    assert kinds == {
        "C": PublicNameKind.CLASS,
        "C.C": PublicNameKind.METHOD,
        "C.M": PublicNameKind.METHOD,
        "C.K": PublicNameKind.ENUM_VALUE,
        "C.Inner": PublicNameKind.CLASS,
        "C.Inner.Go": PublicNameKind.METHOD,
        "S": PublicNameKind.CLASS,
        "R": PublicNameKind.CLASS,
        "I": PublicNameKind.CLASS,
        "I.Do": PublicNameKind.METHOD,
        "E": PublicNameKind.CLASS,
        "E.X": PublicNameKind.ENUM_VALUE,
        "E.Y": PublicNameKind.ENUM_VALUE,
    }
    assert (found["C.M"].path, found["C.M"].line) == ("A.cs", 4)


def test_private_members_unqualified_members_and_members_of_a_private_type_are_left_out() -> None:
    source = (
        "public class C {\n"
        "  private void A() {}\n"
        "  void B() {}\n"
        "  private protected void D() {}\n"
        "  internal void I() {}\n"
        "  protected void P() {}\n"
        "  private class Hidden { public void Go() {} }\n"
        "  class Plain { public void Go() {} }\n"
        "}\n"
        "class Top { public void Go() {} }\n"
        "file class F { public void Go() {} }\n"
    )
    assert set(_added(source)) == {"C", "C.I", "C.P", "Top", "Top.Go"}


def test_interface_and_enum_members_are_public_without_a_modifier() -> None:
    found = _added("public interface I { void Do(); }\ninternal enum E { A }\n")
    assert set(found) == {"I", "I.Do", "E", "E.A"}


def test_private_const_is_left_out() -> None:
    assert set(_added("public class C { private const int A = 1; const int B = 2; }\n")) == {"C"}


def test_partial_types_and_overloads_merge_into_one_name() -> None:
    first = "public partial class C { public void M(int a) {} private partial void H(); }\n"
    second = "public partial class C { public void M(string a) {} public void N() {} }\n"
    found = _names({}, {"A.cs": first, "B.cs": second})
    assert sorted(found) == ["C", "C.M", "C.N"]
    assert (found["C"].path, found["C"].line) == ("A.cs", 1)
    assert found["C.N"].path == "B.cs"


def test_a_name_that_was_there_before_is_not_added() -> None:
    before = {"A.cs": "public class C { void M() {} }\n"}
    after = {"A.cs": "public class C { public void M() {} public void N() {} }\n"}
    assert set(_names(before, after)) == {"C.N"}


def test_a_name_defined_in_an_unchanged_file_is_left_out() -> None:
    after = {"A.cs": "public class A {}\n", "B.cs": "public class B {}\n"}
    assert set(_names({}, after, {"A.cs"})) == {"A"}


def test_declarations_inside_a_body_are_not_definitions() -> None:
    source = "public class C { public void M() { void Local() {} var f = () => 1; } }\n"
    assert set(_added(source)) == {"C", "C.M"}


def test_method_callers_are_counted_by_name() -> None:
    others = {
        "B.cs": (
            "class B {\n  void Run(C c) { c.Send(); Send(); }\n  void Other() { Hidden(); }\n}\n"
        )
    }
    found = _added("public class C { public void Send() {} private void Hidden() {} }\n", others)
    assert "C.Hidden" not in found
    assert found["C.Send"].callers == ("B.cs の B.Run",)


def test_constructor_callers_are_creations_of_the_type() -> None:
    others = {"B.cs": "class B { void Run() { var c = new C(1); } }\n"}
    found = _added("public class C { public C(int a) {} }\n", others)
    assert found["C.C"].kind is PublicNameKind.METHOD
    assert found["C.C"].callers == ("B.cs の B.Run",)
    assert found["C"].callers == ("B.cs の B.Run",)


def test_implicit_new_counts_only_with_a_declared_type_name() -> None:
    others = {
        "B.cs": (
            "class B {\n"
            "  C field = new();\n"
            "  void Local() { C local = new(); }\n"
            "  void Arg() { Take(new()); }\n"
            "  C Ret() { return new(); }\n"
            "  void Assign() { C x; x = new(); }\n"
            "  void Param(C p = new()) {}\n"
            "}\n"
        )
    }
    found = _added("public class C { public C() {} }\n", others)
    assert found["C.C"].callers == ("B.cs の B", "B.cs の B.Local")


def test_type_callers_include_bases_creations_and_type_names() -> None:
    others = {
        "B.cs": (
            "class D : Base, IThing {}\n"
            "class U {\n"
            "  void Make() { var b = new Base(); }\n"
            "  void Take(Base b) {}\n"
            "}\n"
        )
    }
    source = "public class Base {}\npublic interface IThing {}\n"
    found = _added(source, others)
    assert found["Base"].callers == ("B.cs の D", "B.cs の U.Make", "B.cs の U.Take")
    assert found["IThing"].callers == ("B.cs の D",)


def test_enum_value_callers_are_type_dot_value_references() -> None:
    others = {"B.cs": "class B { Level F() { return Level.Trace; } int G() => Other.Trace; }\n"}
    found = _added("public enum Level { Trace, Fatal }\n", others)
    assert found["Level.Trace"].callers == ("B.cs の B.F",)
    assert found["Level.Fatal"].callers == ()


def test_const_callers_are_member_references() -> None:
    others = {"B.cs": "class B { int F() { return C.K; } }\n"}
    found = _added("public class C { public const int K = 1; }\n", others)
    assert found["C.K"].callers == ("B.cs の B.F",)


def test_recursion_is_not_a_caller() -> None:
    found = _added("public class C { public void Loop() { Loop(); } }\n")
    assert found["C.Loop"].callers == ()
    assert found["C"].callers == ()


def test_method_group_is_not_a_caller() -> None:
    others = {"B.cs": "class B { void Run() { Register(Handle); } }\n"}
    found = _added("public class C { public void Handle() {} }\n", others)
    assert found["C.Handle"].callers == ()


def test_declaration_names_are_not_callers() -> None:
    others = {"B.cs": "class B { int C; void Run(int C) { int M = 1; } }\n"}
    found = _added("public class C { public void M() {} }\n", others)
    assert found["C"].callers == ()
    assert found["C.M"].callers == ()


def test_file_that_does_not_parse_deeply_is_skipped() -> None:
    deep = "class D { int F() { return " + "(" * 20000 + "1" + ")" * 20000 + "; } }\n"
    found = _names({}, {"A.cs": "public class A {}\n", "D.cs": deep}, {"A.cs"})
    assert set(found) == {"A"}
