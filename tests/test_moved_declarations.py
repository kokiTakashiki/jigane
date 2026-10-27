from jigane.policy_materials.moved_declarations import MovedPair, PlacedDeclaration, find_moved


def placed(qualified: str, kind: str, path: str, shape: str) -> PlacedDeclaration[str]:
    return PlacedDeclaration(qualified, kind, path, shape, f"{path}:{qualified}")


def test_same_name_kind_and_shape_in_another_file_is_moved() -> None:
    old = placed("a.helper", "function", "a.py", "def helper(x: int)")
    new = placed("b.helper", "function", "b.py", "def  helper(x:  int)")
    assert find_moved([old], [new]) == [MovedPair(old, new)]


def test_a_different_shape_is_not_moved() -> None:
    old = placed("helper", "function", "a.py", "def helper(x: int)")
    new = placed("helper", "function", "b.py", "def helper(x: str)")
    assert find_moved([old], [new]) == []


def test_a_different_kind_or_name_is_not_moved() -> None:
    old = placed("helper", "function", "a.py", "helper")
    assert find_moved([old], [placed("helper", "variable", "b.py", "helper")]) == []
    assert find_moved([old], [placed("other", "function", "b.py", "helper")]) == []


def test_the_same_path_is_not_moved() -> None:
    old = placed("helper", "function", "a.py", "def helper()")
    assert find_moved([old], [placed("helper", "function", "a.py", "def helper()")]) == []


def test_several_candidates_with_the_same_name_are_not_moved() -> None:
    old = placed("helper", "function", "a.py", "def helper()")
    twins = [
        placed("helper", "function", "b.py", "def helper()"),
        placed("helper", "function", "c.py", "def helper()"),
    ]
    assert find_moved([old], twins) == []
    assert find_moved([old, placed("helper", "function", "d.py", "def helper()")], twins[:1]) == []
