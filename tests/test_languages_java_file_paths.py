import pytest

from jigane.languages.base import FilePathAdapter, FilePathPattern, FileUsage, parse
from jigane.languages.java import JavaAdapter
from jigane.languages.java_file_paths import file_path_patterns


def patterns(code: str) -> list[FilePathPattern]:
    java = JavaAdapter()
    source = code.encode()
    return java.file_path_patterns(parse(java, source).root_node, source, "R.java")


def pairs(code: str) -> list[tuple[str, FileUsage]]:
    return [(p.expression, p.usage) for p in patterns(code)]


def test_java_adapter_offers_the_reader() -> None:
    assert isinstance(JavaAdapter(), FilePathAdapter)


def test_files_reads_and_readers_read() -> None:
    code = (
        "class R { void f(Path p, String s) throws Exception {\n"
        "    Files.readString(p);\n"
        "    Files.readAllLines(p);\n"
        "    Files.readAllBytes(p);\n"
        "    Files.lines(p);\n"
        "    Files.newBufferedReader(p);\n"
        "    Files.newInputStream(p);\n"
        "    Files.exists(p);\n"
        "    new FileReader(s);\n"
        "    new FileInputStream(s);\n"
        "} }\n"
    )
    assert pairs(code) == [("p", FileUsage.READ)] * 7 + [("s", FileUsage.READ)] * 2


def test_files_writes_and_writers_write() -> None:
    code = (
        "class R { void f(Path p, String s) throws Exception {\n"
        "    Files.writeString(p, s);\n"
        "    Files.write(p, s.getBytes());\n"
        "    Files.newBufferedWriter(p);\n"
        "    Files.newOutputStream(p);\n"
        "    Files.createFile(p);\n"
        "    Files.createDirectory(p);\n"
        "    Files.createDirectories(p);\n"
        "    Files.delete(p);\n"
        "    Files.deleteIfExists(p);\n"
        "    new FileWriter(s);\n"
        "    new FileOutputStream(s);\n"
        "} }\n"
    )
    assert pairs(code) == [("p", FileUsage.WRITE)] * 9 + [("s", FileUsage.WRITE)] * 2


def test_move_writes_both_ends_and_copy_reads_then_writes() -> None:
    code = (
        "class R { void f(Path a, Path b) throws Exception {\n"
        "    Files.move(a, b);\n"
        "    Files.copy(a, b);\n"
        "} }\n"
    )
    assert pairs(code) == [
        ("a", FileUsage.WRITE),
        ("b", FileUsage.WRITE),
        ("a", FileUsage.READ),
        ("b", FileUsage.WRITE),
    ]


def test_receiver_is_matched_by_name_only() -> None:
    code = (
        "class R { void f(Path p) throws Exception {\n"
        "    java.nio.file.Files.readString(p);\n"
        "    new java.io.FileWriter(p);\n"
        "    Other.readString(p);\n"
        "} }\n"
    )
    assert pairs(code) == [("p", FileUsage.READ), ("p", FileUsage.WRITE)]


def test_name_building_is_reported_only_with_plus_format_or_formatted() -> None:
    code = (
        "class R { void f(String n) {\n"
        '    Path.of("a/" + n);\n'
        '    Paths.get(String.format("a/%s", n));\n'
        '    new File("a/%s.txt".formatted(n));\n'
        '    Path.of("a/b");\n'
        '    new File("a/b");\n'
        "    Path.of(n);\n"
        "} }\n"
    )
    assert pairs(code) == [
        ('Path.of("a/" + n)', FileUsage.UNKNOWN),
        ('Paths.get(String.format("a/%s", n))', FileUsage.UNKNOWN),
        ('new File("a/%s.txt".formatted(n))', FileUsage.UNKNOWN),
    ]


def test_io_call_is_reported_whatever_its_argument_looks_like() -> None:
    assert pairs('class R { void f() throws Exception { Files.readString(Path.of("x")); } }') == [
        ('Path.of("x")', FileUsage.READ)
    ]


def test_variable_is_traced_one_step_to_the_earlier_initializer() -> None:
    code = (
        "class R { void f(String n) throws Exception {\n"
        '    Path p = Paths.get("a/" + n);\n'
        "    Files.writeString(p, n);\n"
        "} }\n"
    )
    assert pairs(code) == [('Paths.get("a/" + n)', FileUsage.WRITE)]


def test_variable_defined_after_use_or_in_another_method_is_not_traced() -> None:
    code = (
        'class R { void g() { Path p = Path.of("a" + 1); }\n'
        "void f() throws Exception {\n"
        "    Files.readString(p);\n"
        '    Path p = Path.of("b" + 1);\n'
        "} }\n"
    )
    assert pairs(code) == [
        ('Path.of("a" + 1)', FileUsage.UNKNOWN),
        ("p", FileUsage.READ),
        ('Path.of("b" + 1)', FileUsage.UNKNOWN),
    ]


def test_name_building_wrapped_by_a_reported_call_is_not_reported_again() -> None:
    code = (
        "class R { void f(String n) throws Exception {\n"
        '    Files.readString(Path.of("a/" + n));\n'
        "} }\n"
    )
    assert pairs(code) == [('Path.of("a/" + n)', FileUsage.READ)]


def test_chain_is_reported_as_its_outermost_expression_only() -> None:
    code = (
        "class R { void f(Path root, String n) {\n"
        '    Path p = root.resolve("a").resolve(n + ".txt");\n'
        "} }\n"
    )
    assert pairs(code) == [('root.resolve("a").resolve(n + ".txt")', FileUsage.UNKNOWN)]


def test_lambda_belongs_to_the_enclosing_method() -> None:
    code = (
        "class Outer { class R { R() { }\n"
        "void f(String n) {\n"
        '    Runnable r = () -> new File("a/" + n);\n'
        "} } }\n"
    )
    found = patterns(code)
    assert [(p.function, p.line) for p in found] == [("Outer.R.f", 3)]


def test_constructor_is_part_of_the_qualified_name() -> None:
    code = "class R {\n    R(Path p) throws Exception { Files.delete(p); }\n}\n"
    assert [p.function for p in patterns(code)] == ["R.R"]


def test_a_file_too_deep_to_walk_yields_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    java = JavaAdapter()
    source = b"class R { void f(Path p) throws Exception { Files.delete(p); } }"
    root = parse(java, source).root_node

    def explode(_self: object, _rules: object) -> list[FilePathPattern]:
        raise RecursionError

    monkeypatch.setattr("jigane.languages.file_paths_core._Reader.read", explode)
    assert file_path_patterns(root, "R.java") == []
