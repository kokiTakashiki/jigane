from jigane.design.constants import names_holding_stored_file, stored_file_names
from jigane.languages import registry
from jigane.languages.base import StringConstant, StringConstantReader
from jigane.languages.python_constants import string_constants


def test_plain_annotated_and_chained_assignments_are_read() -> None:
    text = 'A = "x"\nB: str = \'y\'\nC = D = "z"\n'
    assert string_constants(text) == [
        StringConstant("A", "x", 1),
        StringConstant("B", "y", 2),
        StringConstant("C", "z", 3),
        StringConstant("D", "z", 3),
    ]


def test_multiline_and_parenthesized_declarations_report_the_statement_line() -> None:
    text = 'A = (\n    "x"\n)\nB: str = \\\n    "y"\nC = "p" "q"\n'
    assert string_constants(text) == [
        StringConstant("A", "x", 1),
        StringConstant("B", "y", 4),
        StringConstant("C", "pq", 6),
    ]


def test_nested_empty_and_non_string_values_are_not_read() -> None:
    text = (
        'E = ""\nN = 1\nF = f"x{N}"\nT = ("x", "y")\nclass K:\n    A = "x"\n'
        'def f():\n    B = "x"\n    return B\nOK = "ok"\n'
    )
    assert string_constants(text) == [StringConstant("OK", "ok", 10)]


def test_assignments_inside_if_and_try_are_read() -> None:
    text = (
        'if TYPE_CHECKING:\n    A = "a"\nelse:\n    B = "b"\n'
        'try:\n    C = "c"\nexcept ImportError:\n    D = "d"\nfinally:\n    E = "e"\n'
    )
    assert [c.name for c in string_constants(text)] == ["A", "B", "C", "D", "E"]


def test_assignments_inside_functions_and_classes_under_if_are_not_read() -> None:
    text = 'if X:\n    def f():\n        A = "a"\n    class K:\n        B = "b"\n    C = "c"\n'
    assert string_constants(text) == [StringConstant("C", "c", 6)]


def test_unreadable_source_has_no_constants() -> None:
    assert string_constants("A = (\n") == []


def test_many_annotation_lines_without_assignment_do_not_hide_the_constant() -> None:
    text = "a: int\n" * 50_000 + 'NAME = "x"\n'
    assert string_constants(text) == [StringConstant("NAME", "x", 50_001)]


def test_stored_file_views() -> None:
    constants = string_constants(
        'A = "notes.json"\nB = "data/notes.json"\nC = "other.txt"\nD = "x y/notes.json"\n'
    )
    assert names_holding_stored_file(constants, "notes.json") == {"A", "B"}
    assert stored_file_names(constants) == {"notes.json"}


def test_try_star_nested_if_and_elif_assignments_are_read() -> None:
    text = (
        'try:\n    A = "a"\nexcept* ValueError:\n    B = "b"\n'
        'if X:\n    if Y:\n        C = "c"\nelif Z:\n    D = "d"\n'
    )
    assert [c.name for c in string_constants(text)] == ["A", "B", "C", "D"]


def test_exclusive_branches_with_the_same_name_come_in_document_order() -> None:
    text = 'if X:\n    A = "first"\nelse:\n    A = "second"\n'
    constants = string_constants(text)
    assert [(c.name, c.value) for c in constants] == [("A", "first"), ("A", "second")]
    assert {c.name: c.value for c in constants} == {"A": "second"}


def test_python_adapter_exposes_the_constant_reader() -> None:
    adapter = registry.adapter_for("a.py")
    assert isinstance(adapter, StringConstantReader)
    assert adapter.string_constants('A = "x"\n') == [StringConstant("A", "x", 1)]
