from jigane.gates.line_scope import AllLines, ChangedLines


def test_all_lines_covers_every_line_of_every_path() -> None:
    scope = AllLines()
    assert scope.covers("src/a.py", 1)
    assert scope.covers("src/other.py", 99)


def test_changed_lines_covers_only_the_changed_lines_of_a_listed_path() -> None:
    scope = ChangedLines({"src/a.py": frozenset({2, 3})})
    assert scope.covers("src/a.py", 2)
    assert not scope.covers("src/a.py", 4)


def test_changed_lines_treats_a_path_missing_from_them_as_unchanged() -> None:
    scope = ChangedLines({"src/a.py": frozenset({2})})
    assert not scope.covers("src/b.py", 2)
