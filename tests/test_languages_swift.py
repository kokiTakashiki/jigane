from fnmatch import fnmatchcase
from pathlib import Path

from jigane.judgment_map.configuration import DEFAULT_EXCLUDE
from jigane.languages.base import Declaration, Span, Visibility, parse
from jigane.languages.registry import adapter_for, is_build_path, is_test_path
from jigane.languages.swift import SwiftAdapter

adapter = SwiftAdapter()


def declarations(code: str) -> dict[str, Declaration]:
    source = code.encode()
    found = adapter.declarations(parse(adapter, source).root_node, source)
    return {declaration.qualified_name: declaration for declaration in found}


def test_visibility_of_top_level_functions() -> None:
    found = declarations(
        "public func a() {}\nopen class B {}\nfunc c() {}\n"
        "internal func d() {}\nprivate func e() {}\nfileprivate func f() {}\n"
    )
    assert found["a()"].is_public
    assert found["B"].is_public
    assert found["c()"].is_public
    assert found["d()"].is_public
    assert not found["e()"].is_public
    assert not found["f()"].is_public
    assert found["a()"].kind == "function"


def test_private_extension_hides_members_without_modifier() -> None:
    found = declarations(
        "private extension Foo {\n    func a() {}\n    public func b() {}\n}\n"
        "fileprivate extension Foo { func c() {} }\n"
        "extension Foo {\n    func d() {}\n    private func e() {}\n}\n"
    )
    assert not found["Foo.a()"].is_public
    assert not found["Foo.c()"].is_public
    assert found["Foo.d()"].is_public
    assert not found["Foo.e()"].is_public


def test_members_of_private_type_are_private() -> None:
    found = declarations(
        "private struct S {\n    func a() {}\n    var b = 1\n    struct In { func c() {} }\n}\n"
    )
    assert not found["S"].is_public
    assert not found["S.a()"].is_public
    assert not found["S.b"].is_public
    assert not found["S.In.c()"].is_public


def test_setter_restriction_does_not_hide_property() -> None:
    found = declarations("struct S {\n    public private(set) var a = 1\n}\n")
    assert found["S.a"].is_public


def test_protocol_requirements_follow_protocol() -> None:
    found = declarations(
        "public protocol P {\n    func m(_ a: Int)\n    var p: Int { get }\n"
        "    init(x: Int)\n}\nprivate protocol Q { func n() }\n"
    )
    assert found["P.m(_:)"].is_public
    assert found["P.p"].is_public
    assert found["P.init(x:)"].is_public
    assert not found["Q.n()"].is_public


def test_kinds() -> None:
    found = declarations(
        "struct S {\n    var a = 1\n    init() {}\n    deinit {}\n"
        "    func f() {}\n    subscript(i: Int) -> Int { 0 }\n"
        "    enum E { case x }\n}\nactor A {}\nprotocol P {}\nenum En {}\n"
        "class C {}\nfunc top() {}\n"
    )
    assert {found[n].kind for n in ("S", "A", "P", "En", "C", "S.E")} == {"class"}
    assert found["top()"].kind == "function"
    assert found["S.f()"].kind == "method"
    assert found["S.init()"].kind == "method"
    assert found["S.deinit"].kind == "method"
    assert found["S.subscript(_:)"].kind == "method"
    assert found["S.a"].kind == "variable"
    assert found["S.E.x"].kind == "variable"


def test_selector_includes_argument_labels() -> None:
    found = declarations(
        "class C {\n    func cancelAll(_ ids: [Int]) {}\n    func reload() {}\n"
        "    init(id: Int, name: String) {}\n"
        "    func move(from a: Int, to b: Int) {}\n"
        "    func g<T>(value: T, rest: Int...) {}\n"
        "    subscript(index i: Int) -> Int { 0 }\n}\n"
    )
    assert set(found) == {
        "C",
        "C.cancelAll(_:)",
        "C.reload()",
        "C.init(id:name:)",
        "C.move(from:to:)",
        "C.g(value:rest:)",
        "C.subscript(index:)",
    }


def test_extension_members_sit_under_extended_type() -> None:
    found = declarations(
        "extension Foo {\n    func bar() {}\n    struct Baz { func q() {} }\n}\n"
        "extension Outer.Inner { var v: Int { 1 } }\n"
    )
    assert set(found) == {"Foo.bar()", "Foo.Baz", "Foo.Baz.q()", "Outer.Inner.v"}


def test_nested_types_and_local_declarations() -> None:
    found = declarations(
        "struct A {\n    struct B {\n        func m() {\n"
        "            func local() {}\n            let x = 1\n        }\n    }\n}\n"
    )
    assert set(found) == {"A", "A.B", "A.B.m()"}


def test_closure_is_not_a_declaration() -> None:
    found = declarations("struct S {\n    let f = { (a: Int) in a }\n}\n")
    assert set(found) == {"S", "S.f"}


def test_enum_cases() -> None:
    found = declarations(
        "public enum E {\n    case a, b(Int)\n    case c = 1\n}\nprivate enum F { case x }\n"
    )
    assert {"E.a", "E.b", "E.c"} <= set(found)
    assert found["E.a"].span == found["E.b"].span == Span(2, 2)
    assert found["E.a"].is_public
    assert not found["F.x"].is_public


def test_multiple_bindings_are_separate_declarations() -> None:
    found = declarations("struct S {\n    var a = 1, b = 2\n    let (p, q) = (1, 2)\n}\n")
    assert {"S.a", "S.b", "S.p", "S.q"} <= set(found)


def test_multiline_signature_and_body() -> None:
    code = "func f(\n    a: Int,\n    b: Int\n) -> Int {\n    let x = a\n    return x + b\n}\n"
    found = declarations(code)["f(a:b:)"]
    assert found.span == Span(1, 7)
    assert found.signature == Span(1, 4)
    assert found.body == Span(5, 7)


def test_attributes_belong_to_signature() -> None:
    code = "@MainActor\n@available(iOS 16, *)\nfunc f() {\n    run()\n}\n"
    found = declarations(code)["f()"]
    assert found.span == Span(1, 5)
    assert found.signature == Span(1, 3)
    assert found.body == Span(4, 5)


def test_one_line_function_has_no_body() -> None:
    found = declarations("func f() -> Int { 1 }\n")["f()"]
    assert found.signature == Span(1, 1)
    assert found.body is None


def test_allman_brace_style() -> None:
    found = declarations("func f()\n{\n    run()\n}\n")["f()"]
    assert found.signature == Span(1, 2)
    assert found.body == Span(3, 4)


def test_protocol_requirements_have_no_body() -> None:
    code = (
        "protocol P {\n    func m(_ a: Int)\n    var p: Int { get set }\n"
        "    subscript(i: Int) -> Int { get }\n    init(x: Int)\n}\n"
    )
    found = declarations(code)
    assert found["P"].signature == Span(1, 1)
    assert found["P"].body == Span(2, 6)
    for name, line in (("P.m(_:)", 2), ("P.p", 3), ("P.subscript(_:)", 4)):
        assert found[name].signature == Span(line, line)
        assert found[name].body is None
    assert found["P.init(x:)"].body is None


def test_stored_property_is_all_signature() -> None:
    found = declarations("struct S {\n    @Published var a: Int =\n        compute()\n}\n")["S.a"]
    assert found.signature == Span(2, 3)
    assert found.body is None


def test_computed_property_splits_signature_and_body() -> None:
    code = "struct S {\n    var v: Int {\n        1\n    }\n    var w: Int { 2 }\n}\n"
    found = declarations(code)
    assert found["S.v"].signature == Span(2, 2)
    assert found["S.v"].body == Span(3, 4)
    assert found["S.w"].body is None


def test_type_signature_and_body() -> None:
    found = declarations("struct S: P {\n    var a = 1\n}\n")["S"]
    assert found.signature == Span(1, 1)
    assert found.body == Span(2, 3)


def test_comment_node_types_match_grammar() -> None:
    source = b"// a\n/* b */\nfunc f() {}\n"
    root = parse(adapter, source).root_node
    types = {c.type for c in root.children if "comment" in c.type}
    assert types <= adapter.comment_node_types
    assert len(types) == 2


def test_if_config_blocks_are_flat() -> None:
    code = "#if DEBUG\npublic func a() {}\n#else\nfunc b() {}\n#endif\n"
    found = declarations(code)
    assert set(found) == {"a()", "b()"}


def test_path_conventions() -> None:
    registered = [adapter]
    assert isinstance(adapter_for("Sources/App/View.swift"), SwiftAdapter)
    assert is_test_path("Tests/AppTests/A.swift", registered)
    assert is_test_path("Sources/FooTests/A.swift", registered)
    assert is_test_path("Sources/AppTests.swift", registered)
    assert is_test_path("Sources/AppTest.swift", registered)
    assert not is_test_path("Sources/App/View.swift", registered)
    for name in (
        "Package.swift",
        "App.xcodeproj/project.pbxproj",
        "Config/Debug.xcconfig",
        "Podfile",
        "Cartfile",
        "project.yml",
    ):
        assert is_build_path(name, registered)
    assert not is_build_path("Sources/App/View.swift", registered)


def test_default_exclude_covers_lock_files() -> None:
    for path in (
        "Package.resolved",
        "App.xcworkspace/xcshareddata/swiftpm/Package.resolved",
        "Podfile.lock",
        "ios/Podfile.lock",
    ):
        assert any(fnmatchcase(path, glob) for glob in DEFAULT_EXCLUDE)


def test_parses_large_realistic_file() -> None:
    path = Path(__file__).parent / "fixtures" / "Large.swift.txt"
    source = path.read_bytes()
    line_count = source.count(b"\n") + 1
    found = declarations(source.decode())
    assert line_count > 300
    assert "TaskStore.reload()" in found
    assert "TaskStore.subscript(_:)" in found
    assert "Array.done()" in found
    assert not found["TaskStore.resetForTesting()"].is_public
    assert "Row11View.helper11(_:label:)" in found
    for declaration in found.values():
        assert 1 <= declaration.span.start_line <= declaration.span.end_line <= line_count


def test_swift_visibility_levels() -> None:
    found = declarations(
        "public func a() {}\nopen class B {}\nfunc c() {}\n"
        "internal func d() {}\nprivate func e() {}\nfileprivate func f() {}\n"
    )
    assert found["a()"].visibility is Visibility.PUBLIC
    assert found["B"].visibility is Visibility.PUBLIC
    assert found["c()"].visibility is Visibility.INTERNAL
    assert found["d()"].visibility is Visibility.INTERNAL
    assert found["e()"].visibility is Visibility.FILE
    assert found["f()"].visibility is Visibility.FILE


def test_public_func_in_internal_struct_is_internal() -> None:
    found = declarations(
        "struct S { public func a() {}\n func b() {}\n private func c() {} }\n"
        "public struct T { func d() {}\n public func e() {} }\n"
        "private struct U { public func f() {} }\n"
    )
    assert found["S.a()"].visibility is Visibility.INTERNAL
    assert found["S.b()"].visibility is Visibility.INTERNAL
    assert found["S.c()"].visibility is Visibility.FILE
    assert found["T.d()"].visibility is Visibility.INTERNAL
    assert found["T.e()"].visibility is Visibility.PUBLIC
    assert found["U.f()"].visibility is Visibility.FILE


def test_protocol_requirements_and_enum_cases_follow_container_level() -> None:
    found = declarations("public protocol P { func m() }\nenum E { case a }\n")
    assert found["P.m()"].visibility is Visibility.PUBLIC
    assert found["E.a"].visibility is Visibility.INTERNAL
