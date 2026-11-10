import pytest

from jigane.languages.base import StoreAccess, StoreAccessReader, StoreFormat, StoreRole
from jigane.languages.swift import SwiftAdapter

adapter = SwiftAdapter()


def _accesses(**sources: str) -> dict[str, StoreAccess]:
    files = {f"{name}.swift": text for name, text in sources.items()}
    return {a.sides[0].function: a for a in adapter.store_accesses(files)}


def test_adapter_satisfies_store_access_reader() -> None:
    assert isinstance(adapter, StoreAccessReader)


def test_set_and_string_for_key_are_the_write_and_read_sides() -> None:
    source = """
struct Settings {
    func save() { UserDefaults.standard.set("v", forKey: "token") }
    func load() -> String? { UserDefaults.standard.string(forKey: "token") }
    func both() {
        UserDefaults.standard.set(1, forKey: "a")
        _ = UserDefaults.standard.integer(forKey: "b")
    }
}
"""
    found = _accesses(s=source)
    save = found["Settings.save"]
    assert save.names == frozenset({"UserDefaults"})
    assert [(s.role, s.keys, s.kind) for s in save.sides] == [
        (StoreRole.WRITE, frozenset({"token"}), StoreFormat.CUSTOM)
    ]
    assert found["Settings.load"].sides[0].role is StoreRole.READ
    both = found["Settings.both"]
    assert [(s.role, s.keys) for s in both.sides] == [
        (StoreRole.WRITE, frozenset({"a"})),
        (StoreRole.READ, frozenset({"b"})),
    ]


def test_keys_resolve_from_constants_and_enum_cases() -> None:
    source = """
enum Key: String {
    case plain
    case renamed = "renamed-value"
}
struct Keys { static let token = "token-value" }
struct Store {
    static let own = "own-value"
    func f() {
        UserDefaults.standard.set(1, forKey: Keys.token)
        UserDefaults.standard.set(1, forKey: own)
        UserDefaults.standard.set(1, forKey: Key.renamed.rawValue)
        UserDefaults.standard.set(1, forKey: Key.plain.rawValue)
    }
}
"""
    side = _accesses(s=source)["Store.f"].sides[0]
    assert side.keys == frozenset({"token-value", "own-value", "renamed-value", "plain"})
    assert side.labels == frozenset({"Keys", "Store", "Key"})


def test_unresolved_key_is_not_a_key_but_the_side_stays() -> None:
    source = """
struct S {
    func f(_ key: String) { UserDefaults.standard.set(1, forKey: key) }
}
"""
    side = _accesses(s=source)["S.f"].sides[0]
    assert side.keys == frozenset()


def test_same_constant_in_two_types_is_dropped_without_a_type() -> None:
    source = """
struct A { static let name = "from-a" }
struct B { static let name = "from-b" }
struct C {
    func f() {
        UserDefaults.standard.set(1, forKey: name)
        UserDefaults.standard.set(1, forKey: A.name)
    }
}
struct D {
    static let name = "own-d"
    func g() { UserDefaults.standard.set(1, forKey: name) }
}
"""
    found = _accesses(s=source)
    assert found["C.f"].sides[0].keys == frozenset({"from-a"})
    assert found["D.g"].sides[0].keys == frozenset({"own-d"})


def test_conflicting_definitions_of_one_name_are_not_guessed() -> None:
    source = """
struct A { static let name = "one" }
extension A { static let name = "two" }
func f() { UserDefaults.standard.set(1, forKey: A.name) }
"""
    assert _accesses(s=source)["f"].sides[0].keys == frozenset()


def test_remove_object_is_a_write() -> None:
    source = """
struct S { func clear() { UserDefaults.standard.removeObject(forKey: "k") } }
"""
    side = _accesses(s=source)["S.clear"].sides[0]
    assert (side.role, side.keys) == (StoreRole.WRITE, frozenset({"k"}))


def test_constants_come_from_other_files() -> None:
    keys = 'struct Keys { static let token = "t" }\n'
    use = "struct S { func f() { _ = UserDefaults.standard.string(forKey: Keys.token) } }\n"
    found = _accesses(a=keys, b=use)
    assert found["S.f"].sides[0].keys == frozenset({"t"})


def test_other_receivers_and_methods_are_not_stores() -> None:
    source = """
struct S {
    func f(defaults: UserDefaults) {
        defaults.set(1, forKey: "k")
        UserDefaults.standard.register(defaults: [:])
    }
}
"""
    assert _accesses(s=source) == {}


def test_file_that_cannot_be_parsed_is_dropped() -> None:
    good = 'struct S { func f() { UserDefaults.standard.set(1, forKey: "k") } }\n'
    bad = "struct T {}\x00"
    accesses = adapter.store_accesses({"good.swift": good, "bad.swift": bad})
    assert {a.path for a in accesses} == {"good.swift"}


def test_computed_property_accessors_are_units() -> None:
    source = """
struct Prefs {
    var theme: String {
        get { UserDefaults.standard.string(forKey: "theme") ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: "theme") }
    }
    var count: Int = 0 {
        willSet { UserDefaults.standard.set(newValue, forKey: "will") }
        didSet { _ = UserDefaults.standard.integer(forKey: "did") }
    }
}
"""
    found = _accesses(s=source)
    assert set(found) == {
        "Prefs.theme.get",
        "Prefs.theme.set",
        "Prefs.count.willSet",
        "Prefs.count.didSet",
    }
    assert [(s.role, s.keys) for s in found["Prefs.theme.get"].sides] == [
        (StoreRole.READ, frozenset({"theme"}))
    ]
    assert [(s.role, s.keys) for s in found["Prefs.theme.set"].sides] == [
        (StoreRole.WRITE, frozenset({"theme"}))
    ]
    assert found["Prefs.count.willSet"].sides[0].role is StoreRole.WRITE
    assert found["Prefs.count.didSet"].sides[0].role is StoreRole.READ


def test_vocabulary_of_a_file_that_fails_midway_is_not_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_arguments: object) -> None:
        raise ValueError

    # 定数を集めた後、同じファイルの列挙の case を集める途中で落ちる
    monkeypatch.setattr("jigane.languages.swift_store_lexicon.Vocabulary._add_cases", fail)
    keys = 'struct Keys { static let token = "t" }\nenum E: String { case a }\n'
    use = "struct S { func f() { _ = UserDefaults.standard.string(forKey: Keys.token) } }\n"
    found = _accesses(keys=keys, use=use)
    assert found["S.f"].sides[0].keys == frozenset()


CODABLE_SOURCE = """
struct Settings: Codable {
    var theme: String
    let size: Int
    var label: String { theme }
    static let version = 1
}
struct Plain { var other: Int }
struct Both: Encodable, Hashable { var one: Int }
struct Store {
    func save(_ settings: Settings) throws {
        let data = try JSONEncoder().encode(settings)
        try data.write(to: URL(fileURLWithPath: "settings.json"))
    }
    func load(data: Data) throws -> Settings {
        return try JSONDecoder().decode(Settings.self, from: data)
    }
    func saveLocal() throws {
        let value: Settings = Settings(theme: "a", size: 1)
        _ = try JSONEncoder().encode(value)
    }
    func saveUntyped() throws {
        let value = Settings(theme: "a", size: 1)
        _ = try JSONEncoder().encode(value)
    }
    func plain(_ p: Plain) throws {
        _ = try JSONEncoder().encode(p)
    }
    func unknown(data: Data) throws {
        _ = try JSONDecoder().decode(Other.self, from: data)
    }
    func mixed(_ s: Settings) throws {
        UserDefaults.standard.set(1, forKey: "token")
        _ = try JSONEncoder().encode(s)
    }
}
"""


def test_encode_and_decode_are_json_sides_keyed_by_the_type_fields() -> None:
    found = _accesses(s=CODABLE_SOURCE)
    save = found["Store.save"]
    assert save.names == frozenset({"Settings の JSON"})
    (write,) = save.sides
    assert (write.role, write.kind, write.keys, write.types) == (
        StoreRole.WRITE,
        StoreFormat.JSON,
        frozenset({"theme", "size"}),
        frozenset(),
    )
    load = found["Store.load"]
    assert load.names == frozenset({"Settings の JSON"})
    (read,) = load.sides
    assert (read.role, read.kind, read.keys, read.types) == (
        StoreRole.READ,
        StoreFormat.JSON,
        frozenset({"theme", "size"}),
        frozenset({"Settings"}),
    )


def test_local_variable_annotation_gives_the_encoded_type() -> None:
    assert _accesses(s=CODABLE_SOURCE)["Store.saveLocal"].names == frozenset({"Settings の JSON"})


def test_encode_without_a_type_annotation_has_no_write_side() -> None:
    assert "Store.saveUntyped" not in _accesses(s=CODABLE_SOURCE)


def test_file_name_does_not_change_the_store_name() -> None:
    # save だけがファイルの名前を書くが、名前は型から決まる
    found = _accesses(s=CODABLE_SOURCE)
    assert found["Store.save"].names == found["Store.load"].names


def test_non_codable_or_unknown_type_has_empty_keys() -> None:
    found = _accesses(s=CODABLE_SOURCE)
    assert found["Store.plain"].names == frozenset({"Plain の JSON"})
    assert found["Store.unknown"].names == frozenset({"Other の JSON"})
    assert found["Store.plain"].sides[0].keys == frozenset()
    assert found["Store.unknown"].sides[0].keys == frozenset()


def test_encodable_among_other_inheritance_counts_as_codable() -> None:
    source = CODABLE_SOURCE + "func f(_ b: Both) throws { _ = try JSONEncoder().encode(b) }\n"
    side = _accesses(s=source)["f"].sides[0]
    assert side.keys == frozenset({"one"})


def test_computed_and_static_properties_are_not_fields() -> None:
    keys = _accesses(s=CODABLE_SOURCE)["Store.save"].sides[0].keys
    assert "label" not in keys
    assert "version" not in keys


def test_one_function_touching_two_stores_does_not_mix_keys() -> None:
    found = [
        a
        for a in adapter.store_accesses({"s.swift": CODABLE_SOURCE})
        if a.sides[0].function == "Store.mixed"
    ]
    by_name = {next(iter(a.names)): a.sides[0].keys for a in found}
    assert by_name == {
        "UserDefaults": frozenset({"token"}),
        "Settings の JSON": frozenset({"theme", "size"}),
    }


KEYCHAIN_SOURCE = """
enum Account: String {
    case token
    case renamed = "renamed-value"
}
struct Names { static let refresh = "refresh-name" }
struct Vault {
    func add(data: Data) {
        let q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "svc",
            kSecAttrAccount as String: "literal-name",
            kSecValueData as String: data,
        ]
        SecItemAdd(q as CFDictionary, nil)
    }
    func read() {
        var q = [kSecAttrAccount: Names.refresh]
        SecItemCopyMatching(q as CFDictionary, &result)
    }
    func direct() {
        SecItemCopyMatching([kSecAttrAccount: Account.renamed.rawValue] as CFDictionary, &r)
        SecItemCopyMatching([kSecAttrAccount: Account.token.rawValue] as CFDictionary, &r)
    }
    func update(account: String) {
        let q: [String: Any] = [kSecAttrAccount as String: "upd"]
        let attrs: [String: Any] = [kSecAttrAccount as String: "ignored"]
        SecItemUpdate(q as CFDictionary, attrs as CFDictionary)
    }
    func remove() {
        SecItemDelete([kSecAttrAccount as String: "gone"] as CFDictionary)
    }
    func unresolved(account: String) {
        let q: [String: Any] = [kSecAttrAccount as String: account]
        SecItemAdd(q as CFDictionary, nil)
    }
    func withoutService() {
        let q: [String: Any] = [kSecAttrAccount as String: "literal-name"]
        SecItemAdd(q as CFDictionary, nil)
    }
    func mixed() {
        UserDefaults.standard.set(1, forKey: "defaults-key")
        SecItemAdd([kSecAttrAccount as String: "keychain-key"] as CFDictionary, nil)
    }
}
"""


def test_keychain_add_is_the_write_side_and_copy_matching_the_read_side() -> None:
    found = _accesses(s=KEYCHAIN_SOURCE)
    add = found["Vault.add"]
    assert add.names == frozenset({"Keychain"})
    assert [(s.role, s.keys, s.kind) for s in add.sides] == [
        (StoreRole.WRITE, frozenset({"literal-name"}), StoreFormat.CUSTOM)
    ]
    read = found["Vault.read"]
    assert [(s.role, s.keys) for s in read.sides] == [
        (StoreRole.READ, frozenset({"refresh-name"}))
    ]


def test_keychain_account_resolves_from_constant_and_enum_rawvalue() -> None:
    sides = _accesses(s=KEYCHAIN_SOURCE)["Vault.direct"].sides
    assert [s.keys for s in sides] == [frozenset({"renamed-value", "token"})]


def test_keychain_dictionary_written_inline_and_via_one_variable_both_resolve() -> None:
    found = _accesses(s=KEYCHAIN_SOURCE)
    assert found["Vault.remove"].sides[0].keys == frozenset({"gone"})
    assert found["Vault.add"].sides[0].keys == frozenset({"literal-name"})


def test_keychain_update_and_delete_are_write_sides_and_update_reads_only_the_query() -> None:
    found = _accesses(s=KEYCHAIN_SOURCE)
    update = found["Vault.update"].sides
    assert [(s.role, s.keys) for s in update] == [(StoreRole.WRITE, frozenset({"upd"}))]
    assert found["Vault.remove"].sides[0].role is StoreRole.WRITE


def test_keychain_service_does_not_change_the_store_name() -> None:
    found = _accesses(s=KEYCHAIN_SOURCE)
    assert found["Vault.add"].names == found["Vault.withoutService"].names
    assert found["Vault.add"].sides[0].keys == found["Vault.withoutService"].sides[0].keys


def test_keychain_unresolved_account_leaves_the_side_with_no_keys() -> None:
    side = _accesses(s=KEYCHAIN_SOURCE)["Vault.unresolved"].sides[0]
    assert side.role is StoreRole.WRITE
    assert side.keys == frozenset()


def test_keychain_value_data_is_not_read_as_a_key() -> None:
    source = """
func f() {
    let q: [String: Any] = [kSecValueData as String: "secret-value", kSecClass as String: "c"]
    SecItemAdd(q as CFDictionary, nil)
}
"""
    assert _accesses(s=source)["f"].sides[0].keys == frozenset()


def test_keychain_and_user_defaults_in_one_function_do_not_mix_keys() -> None:
    found = [
        a
        for a in adapter.store_accesses({"s.swift": KEYCHAIN_SOURCE})
        if a.sides[0].function == "Vault.mixed"
    ]
    by_name = {next(iter(a.names)): a.sides[0].keys for a in found}
    assert by_name == {
        "UserDefaults": frozenset({"defaults-key"}),
        "Keychain": frozenset({"keychain-key"}),
    }
