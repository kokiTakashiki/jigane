import pytest

from jigane.languages.base import FilePathAdapter, FilePathPattern, FileUsage, parse
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.csharp_file_paths import file_path_patterns


def patterns(code: str) -> list[FilePathPattern]:
    csharp = CSharpAdapter()
    source = code.encode()
    return csharp.file_path_patterns(parse(csharp, source).root_node, source, "R.cs")


def pairs(code: str) -> list[tuple[str, FileUsage]]:
    return [(p.expression, p.usage) for p in patterns(code)]


def body(statements: str) -> str:
    return "class R { void F(string p, string s) {\n" + statements + "\n} }\n"


def test_csharp_adapter_offers_the_reader() -> None:
    assert isinstance(CSharpAdapter(), FilePathAdapter)


def test_file_reads_read() -> None:
    code = body(
        "File.ReadAllText(p); File.ReadAllLines(p); File.ReadAllBytes(p); File.ReadLines(p);\n"
        "File.OpenRead(p); File.OpenText(p); File.Exists(p); Directory.Exists(p);\n"
        "new StreamReader(s);"
    )
    assert pairs(code) == [("p", FileUsage.READ)] * 8 + [("s", FileUsage.READ)]


def test_file_writes_write() -> None:
    code = body(
        "File.WriteAllText(p, s); File.WriteAllLines(p, s); File.WriteAllBytes(p, s);\n"
        "File.AppendAllText(p, s); File.AppendAllLines(p, s); File.Create(p);\n"
        "File.CreateText(p); File.AppendText(p); File.OpenWrite(p); File.Delete(p);\n"
        "Directory.CreateDirectory(p); Directory.Delete(p); new StreamWriter(s);"
    )
    assert pairs(code) == [("p", FileUsage.WRITE)] * 12 + [("s", FileUsage.WRITE)]


def test_async_versions_follow_the_sync_ones() -> None:
    code = body(
        "await File.ReadAllTextAsync(p); await File.ReadAllLinesAsync(p);\n"
        "await File.ReadAllBytesAsync(p); File.ReadLinesAsync(p);\n"
        "await File.WriteAllTextAsync(p, s); await File.WriteAllLinesAsync(p, s);\n"
        "await File.WriteAllBytesAsync(p, s); await File.AppendAllTextAsync(p, s);\n"
        "await File.AppendAllLinesAsync(p, s);"
    )
    assert pairs(code) == [("p", FileUsage.READ)] * 4 + [("p", FileUsage.WRITE)] * 5


def test_move_writes_both_ends_and_copy_reads_then_writes() -> None:
    code = body("File.Move(p, s); Directory.Move(p, s); File.Copy(p, s);")
    assert pairs(code) == [
        ("p", FileUsage.WRITE),
        ("s", FileUsage.WRITE),
        ("p", FileUsage.WRITE),
        ("s", FileUsage.WRITE),
        ("p", FileUsage.READ),
        ("s", FileUsage.WRITE),
    ]


def test_receiver_is_matched_by_name_only() -> None:
    code = body(
        "System.IO.File.ReadAllText(p);\n"
        "new System.IO.StreamWriter(p);\n"
        "Other.ReadAllText(p);\n"
        "ReadAllText(p);"
    )
    assert pairs(code) == [("p", FileUsage.READ), ("p", FileUsage.WRITE)]


def test_file_stream_is_not_reported() -> None:
    assert pairs(body("new FileStream(p, FileMode.Open);")) == []


def test_name_building_is_reported_only_with_interpolation_or_plus() -> None:
    code = body(
        'Path.Combine("a", $"{s}.txt");\n'
        'System.IO.Path.Join("a/" + s);\n'
        'Path.Combine("a", "b");\n'
        "Path.Combine(p, s);"
    )
    assert pairs(code) == [
        ('Path.Combine("a", $"{s}.txt")', FileUsage.UNKNOWN),
        ('System.IO.Path.Join("a/" + s)', FileUsage.UNKNOWN),
    ]


def test_io_call_is_reported_whatever_its_argument_looks_like() -> None:
    code = body('File.ReadAllText(Path.Combine("a", "b"));')
    assert pairs(code) == [('Path.Combine("a", "b")', FileUsage.READ)]


def test_named_argument_reports_its_value() -> None:
    assert pairs(body("new StreamReader(path: p);")) == [("p", FileUsage.READ)]


def test_variable_is_traced_one_step_to_the_earlier_initializer() -> None:
    code = body('var t = Path.Combine("a", s + ".txt");\nFile.WriteAllText(t, s);')
    assert pairs(code) == [('Path.Combine("a", s + ".txt")', FileUsage.WRITE)]


def test_variable_defined_after_use_or_in_another_method_is_not_traced() -> None:
    code = (
        'class R { void G() { var t = Path.Combine("a", $"{1}"); }\n'
        "void F() {\n"
        "    File.ReadAllText(t);\n"
        '    var t = Path.Combine("b", $"{2}");\n'
        "} }\n"
    )
    assert pairs(code) == [
        ('Path.Combine("a", $"{1}")', FileUsage.UNKNOWN),
        ("t", FileUsage.READ),
        ('Path.Combine("b", $"{2}")', FileUsage.UNKNOWN),
    ]


def test_name_building_wrapped_by_a_reported_call_is_not_reported_again() -> None:
    code = body('File.ReadAllText(Path.Combine("a", $"{s}"));')
    assert pairs(code) == [('Path.Combine("a", $"{s}")', FileUsage.READ)]


def test_chain_is_reported_as_its_outermost_expression_only() -> None:
    code = body('var t = Path.Combine(Path.Combine("a", s + "b"), "c");')
    assert pairs(code) == [('Path.Combine(Path.Combine("a", s + "b"), "c")', FileUsage.UNKNOWN)]


def test_lambda_belongs_to_the_enclosing_method() -> None:
    code = (
        "class Outer { class R { R() { }\n"
        "void F(string s) {\n"
        '    Func<string> f = () => Path.Combine("a", $"{s}");\n'
        "} } }\n"
    )
    found = patterns(code)
    assert [(p.function, p.line) for p in found] == [("Outer.R.F", 3)]


def test_constructor_and_accessors_are_part_of_the_qualified_name() -> None:
    code = (
        "class R {\n"
        "    R(string p) { File.Delete(p); }\n"
        "    string P {\n"
        "        get { return File.ReadAllText(a); }\n"
        "        set { File.WriteAllText(b, value); }\n"
        "    }\n"
        "}\n"
    )
    assert [p.function for p in patterns(code)] == ["R.R", "R.P.get", "R.P.set"]


def test_file_scoped_namespace_does_not_change_the_qualified_name() -> None:
    code = "namespace N;\nclass R { void F(string p) { File.Delete(p); } }\n"
    assert [p.function for p in patterns(code)] == ["R.F"]


def test_a_file_too_deep_to_walk_yields_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    csharp = CSharpAdapter()
    source = b"class R { void F(string p) { File.Delete(p); } }"
    root = parse(csharp, source).root_node

    def explode(_self: object, _rules: object) -> list[FilePathPattern]:
        raise RecursionError

    monkeypatch.setattr("jigane.languages.file_paths_core._Reader.read", explode)
    assert file_path_patterns(root, "R.cs") == []
