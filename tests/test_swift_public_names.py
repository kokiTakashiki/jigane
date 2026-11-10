from collections.abc import Collection, Mapping

from tree_sitter import Node

from jigane.languages.base import PublicName, PublicNameKind, PublicNameReader, parse
from jigane.languages.swift import SwiftAdapter
from jigane.languages.swift_public_names import added_public_names_of

_ADAPTER = SwiftAdapter()


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


def test_swift_adapter_is_a_public_name_reader() -> None:
    assert isinstance(_ADAPTER, PublicNameReader)


def test_function_is_added_and_callers_are_counted_by_name() -> None:
    after = {
        "A.swift": "func send() {}\nprivate func hidden() {}\n",
        "B.swift": "func run() { send(); hidden() }\nstruct T { func go() { send() } }\n",
    }
    found = _names({}, after)
    assert "hidden" not in found
    send = found["send"]
    assert send.kind is PublicNameKind.FUNCTION
    assert (send.path, send.line) == ("A.swift", 1)
    assert send.callers == ("B.swift の run", "B.swift の T.go")


def test_recursion_is_not_a_caller() -> None:
    found = _names({}, {"A.swift": "func loop() { loop() }\n"})
    assert found["loop"].callers == ()


def test_methods_init_and_types_are_added() -> None:
    after = {
        "A.swift": (
            "struct S {\n"
            "    init(a: Int) {}\n"
            "    func m(z: Int) {}\n"
            "    private func h() {}\n"
            "}\n"
            "protocol P { func f() }\n"
            "actor Z {}\n"
        ),
        "B.swift": "func use(o: S) { let s = S(a: 1); s.m(z: 2); o.m(z: 3) }\n",
    }
    found = _names({}, after)
    assert found["S"].kind is PublicNameKind.CLASS
    assert found["S"].callers == ("B.swift の use",)
    assert found["S.m"].kind is PublicNameKind.METHOD
    assert found["S.m"].callers == ("B.swift の use",)
    assert found["S.init"].kind is PublicNameKind.METHOD
    assert found["S.init"].callers == ("B.swift の use",)
    assert {"P", "P.f", "Z"} <= set(found)
    assert "S.h" not in found


def test_private_type_hides_its_members() -> None:
    found = _names(
        {}, {"A.swift": "fileprivate struct S { func m() {} }\nprivate enum E { case a }\n"}
    )
    assert found == {}


def test_enum_case_callers_are_references_to_the_case() -> None:
    after = {
        "A.swift": "enum Mode { case fast, slow(Int) }\n",
        "B.swift": "func pick() -> Mode { Mode.fast }\n",
    }
    found = _names({}, after)
    assert found["Mode.fast"].kind is PublicNameKind.ENUM_VALUE
    assert found["Mode.fast"].callers == ("B.swift の pick",)
    assert found["Mode.slow"].callers == ()


def test_added_parameter_is_matched_by_label() -> None:
    before = {"A.swift": "func f(a: Int) {}\n"}
    after = {
        "A.swift": "func f(a: Int, flag: Bool, _ skip: Int) {}\n",
        "B.swift": "func g() { f(a: 1, flag: true); f(a: 2) }\nfunc h() { f(a: 3) }\n",
    }
    found = _names(before, after, {"A.swift"})
    assert set(found) == {"f.flag"}
    flag = found["f.flag"]
    assert flag.kind is PublicNameKind.PARAMETER
    assert flag.callers == ("B.swift の g",)


def test_unchanged_files_and_unreadable_sources_are_ignored() -> None:
    before: dict[str, str] = {}
    after = {"A.swift": "func a() {}\n", "B.swift": "func b( {{{ \n"}
    found = _added(before, after, {"B.swift"})
    assert [n.name for n in found] == []


def test_a_file_that_cannot_be_parsed_is_skipped_and_others_remain() -> None:
    def parse_source(source: str) -> Node:
        if "boom" in source:
            raise RecursionError
        return parse(_ADAPTER, source.encode()).root_node

    after = {"A.swift": "func a() {}\n", "B.swift": "func boom() {}\n"}
    found = added_public_names_of(parse_source, {}, after, set(after))
    assert [n.name for n in found] == ["a"]


def test_extension_method_is_added_under_the_extended_type_and_counted() -> None:
    after = {
        "A.swift": "extension Foo { func bar() {} }\n",
        "B.swift": "func run(f: Foo) { f.bar() }\n",
    }
    found = _names({}, after)
    assert "Foo" not in found
    bar = found["Foo.bar"]
    assert bar.kind is PublicNameKind.METHOD
    assert (bar.path, bar.line) == ("A.swift", 1)
    assert bar.callers == ("B.swift の run",)


def test_private_extension_hides_its_members() -> None:
    after = {"A.swift": "private extension Foo { func bar() {} }\n"}
    assert "Foo.bar" not in _names({}, after)


def test_type_counts_annotations_and_references_by_name() -> None:
    after = {
        "A.swift": (
            'enum Schema { enum Key { static let id = "id" } }\n'
            "struct Draft {}\n"
            "struct Maker { static func make() -> Int { 1 } }\n"
            "struct Meta {}\n"
            "protocol Unused {}\n"
        ),
        "B.swift": (
            "struct User {\n"
            "    let draft: Draft?\n"
            "    func key() -> String { Schema.Key.id }\n"
            "    func made() -> Int { Maker.make() }\n"
            "    func meta() -> Any { Meta.self }\n"
            "}\n"
        ),
    }
    found = _names({}, after)
    assert found["Draft"].callers == ("B.swift の User",)
    assert found["Schema.Key"].callers == ("B.swift の User.key",)
    assert found["Schema"].callers == ("B.swift の User.key",)
    assert found["Maker"].callers == ("B.swift の User.made",)
    assert found["Meta"].callers == ("B.swift の User.meta",)
    assert found["Unused"].callers == ()


def test_type_named_only_inside_itself_has_no_caller() -> None:
    after = {
        "A.swift": "struct Node { let next: [Node]; func copy() -> Node { Node(next: []) } }\n"
    }
    assert _names({}, after)["Node"].callers == ()


def test_type_counts_uses_of_another_definition_with_the_same_name() -> None:
    after = {
        "A.swift": "struct Badge {}\n",
        "B.swift": "struct Badge {}\nstruct Tile { let badge: Badge }\n",
    }
    found = _names({}, after, {"A.swift"})
    assert found["Badge"].callers == ("B.swift の Tile",)


def test_shorthand_enum_value_is_not_counted() -> None:
    after = {
        "A.swift": "enum Kind { case alpha }\n",
        "B.swift": "func f() -> Kind { .alpha }\n",
    }
    found = _names({}, after)
    assert found["Kind"].callers == ("B.swift の f",)
    assert found["Kind.alpha"].callers == ()


def test_declared_names_and_labels_are_not_counted_as_uses_of_a_type() -> None:
    after = {
        "A.swift": "struct Draft {}\n",
        "B.swift": (
            "func Draft() {}\n"
            "func f(Draft value: Int) {}\n"
            "enum Kind { case Draft }\n"
            "let Draft = 1\n"
            "func g() { f(Draft: 1) }\n"
            "struct User { let draft: Draft? }\n"
        ),
    }
    assert _names({}, after, {"A.swift"})["Draft"].callers == ("B.swift の User",)


def test_same_top_level_function_in_two_files_is_two_names() -> None:
    after = {"A.swift": "func send() {}\n", "B.swift": "func send() {}\n"}
    found = _added({}, after, set(after))
    assert [(n.name, n.path) for n in found] == [("send", "A.swift"), ("send", "B.swift")]
