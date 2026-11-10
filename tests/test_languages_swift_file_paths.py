import pytest

from jigane.languages.base import FilePathAdapter, FilePathPattern, FileUsage, parse
from jigane.languages.swift import SwiftAdapter


def patterns(code: str) -> list[FilePathPattern]:
    swift = SwiftAdapter()
    source = code.encode()
    return swift.file_path_patterns(parse(swift, source).root_node, source, "r.swift")


def pairs(code: str) -> list[tuple[str, FileUsage]]:
    return [(p.expression, p.usage) for p in patterns(code)]


def test_swift_adapter_offers_the_reader() -> None:
    assert isinstance(SwiftAdapter(), FilePathAdapter)


def test_data_and_string_initializers_read() -> None:
    code = (
        "func f(u: URL, p: String) {\n"
        "    let d = try Data(contentsOf: u)\n"
        "    let a = try String(contentsOf: u)\n"
        "    let b = try String(contentsOfFile: p)\n"
        "}\n"
    )
    assert pairs(code) == [("u", FileUsage.READ), ("u", FileUsage.READ), ("p", FileUsage.READ)]


def test_write_methods_write_and_inout_stream_is_ignored() -> None:
    code = (
        "func f(u: URL, p: String) {\n"
        "    try data.write(to: u)\n"
        "    try text.write(toFile: p, atomically: true)\n"
        "    text.write(to: &output)\n"
        "}\n"
    )
    assert pairs(code) == [("u", FileUsage.WRITE), ("p", FileUsage.WRITE)]


def test_file_manager_usage_split() -> None:
    code = (
        "func f(a: String, b: URL, c: URL) {\n"
        "    FileManager.default.createFile(atPath: a, contents: nil)\n"
        "    FileManager.default.contents(atPath: a)\n"
        "    FileManager.default.fileExists(atPath: a)\n"
        "    try FileManager.default.removeItem(at: b)\n"
        "    try FileManager.default.createDirectory(atPath: a)\n"
        "    try FileManager.default.moveItem(at: b, to: c)\n"
        "    try FileManager.default.copyItem(at: b, to: c)\n"
        "}\n"
    )
    assert pairs(code) == [
        ("a", FileUsage.WRITE),
        ("a", FileUsage.READ),
        ("a", FileUsage.READ),
        ("b", FileUsage.WRITE),
        ("a", FileUsage.WRITE),
        ("b", FileUsage.WRITE),
        ("c", FileUsage.WRITE),
        ("b", FileUsage.READ),
        ("c", FileUsage.WRITE),
    ]


def test_urls_for_in_is_read_with_whole_call() -> None:
    call = "FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)"
    code = f"func f() {{ _ = {call} }}\n"
    assert pairs(code) == [(call, FileUsage.READ)]


def test_variable_argument_is_resolved_one_step_and_function_is_qualified() -> None:
    code = (
        "struct Exporter {\n"
        "    func save(name: String) throws {\n"
        '        let target = base.appendingPathComponent("\\(name).png")\n'
        "        try data.write(to: target)\n"
        "    }\n"
        "}\n"
    )
    result = patterns(code)
    assert [(p.function, p.expression, p.usage, p.line) for p in result] == [
        (
            "Exporter.save",
            'base.appendingPathComponent("\\(name).png")',
            FileUsage.WRITE,
            4,
        )
    ]


def test_appending_chain_is_one_expression_taken_verbatim() -> None:
    code = (
        "func f(i: Int) {\n"
        '    let u = base.appending(path: "p_\\(i)").appendingPathExtension("png")\n'
        "}\n"
    )
    assert pairs(code) == [
        ('base.appending(path: "p_\\(i)").appendingPathExtension("png")', FileUsage.UNKNOWN)
    ]


def test_url_initializers_with_string_building_are_unknown() -> None:
    code = (
        "func f(n: String) {\n"
        '    _ = URL(fileURLWithPath: "out/\\(n).txt")\n'
        '    _ = URL(filePath: dir + "/x")\n'
        '    _ = URL(string: "a/\\(n)")\n'
        "}\n"
    )
    assert pairs(code) == [
        ('URL(fileURLWithPath: "out/\\(n).txt")', FileUsage.UNKNOWN),
        ('URL(filePath: dir + "/x")', FileUsage.UNKNOWN),
        ('URL(string: "a/\\(n)")', FileUsage.UNKNOWN),
    ]


def test_io_argument_is_not_reported_twice() -> None:
    code = 'func f(n: String) { try? Data(contentsOf: URL(fileURLWithPath: "\\(n)")) }\n'
    assert pairs(code) == [('URL(fileURLWithPath: "\\(n)")', FileUsage.READ)]


def test_unrelated_calls_are_ignored() -> None:
    code = (
        "func f(u: URL) {\n"
        '    _ = URL(fileURLWithPath: "plain.txt")\n'
        '    _ = base.appendingPathComponent("plain")\n'
        "    other.removeItem(at: u)\n"
        "    cache.fileExists(atPath: u.path)\n"
        "    print(u)\n"
        "}\n"
    )
    assert patterns(code) == []


def test_a_file_too_deep_to_walk_yields_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    swift = SwiftAdapter()
    source = b"func f(u: URL) { try? FileManager.default.removeItem(at: u) }"
    root = parse(swift, source).root_node

    def explode(_self: object, _rules: object) -> list[FilePathPattern]:
        raise RecursionError

    monkeypatch.setattr("jigane.languages.file_paths_core._Reader.read", explode)
    assert swift.file_path_patterns(root, source, "r.swift") == []
