from pathlib import Path

from jigane.languages.base import (
    Branch,
    BranchArm,
    Declaration,
    Return,
    Span,
    Visibility,
    parse,
)
from jigane.languages.python import PythonAdapter

adapter = PythonAdapter()


def declarations(code: str) -> dict[str, Declaration]:
    source = code.encode()
    found = adapter.declarations(parse(adapter, source).root_node, source)
    return {declaration.qualified_name: declaration for declaration in found}


def test_public_and_private_functions() -> None:
    found = declarations("def a(): pass\ndef _b(): pass\n")
    assert found["a"].is_public
    assert not found["_b"].is_public
    assert found["a"].kind == "function"


def test_all_narrows_public_names() -> None:
    found = declarations("__all__ = ['a']\ndef a(): pass\ndef b(): pass\n")
    assert found["a"].is_public
    assert not found["b"].is_public


def test_all_as_tuple() -> None:
    found = declarations("__all__ = ('a',)\ndef a(): pass\ndef b(): pass\n")
    assert found["a"].is_public
    assert not found["b"].is_public


def test_dynamic_all_is_ignored() -> None:
    found = declarations("__all__ = names()\ndef b(): pass\n")
    assert found["b"].is_public


def test_special_method_is_public() -> None:
    found = declarations("class A:\n    def __init__(self): pass\n    def _h(self): pass\n")
    assert found["A.__init__"].is_public
    assert found["A.__init__"].kind == "method"
    assert not found["A._h"].is_public


def test_method_of_private_class_is_private() -> None:
    found = declarations("class _A:\n    def run(self): pass\n")
    assert not found["_A"].is_public
    assert not found["_A.run"].is_public


def test_nested_class_qualified_name() -> None:
    found = declarations("class A:\n    class B:\n        def m(self): pass\n")
    assert found["A.B.m"].is_public
    assert found["A.B"].kind == "class"


def test_function_inside_function_is_not_reported() -> None:
    found = declarations("def f():\n    def g(): pass\n    x = 1\n")
    assert list(found) == ["f"]


def test_decorated_span_and_signature() -> None:
    code = "@d\n@e(1)\ndef f(a):\n    return a\n"
    found = declarations(code)["f"]
    assert found.span == Span(1, 4)
    assert found.signature == Span(1, 3)
    assert found.body == Span(4, 4)


def test_multiline_signature() -> None:
    code = "def f(\n    a,\n    b,\n) -> int:\n    x = 1\n    return x\n"
    found = declarations(code)["f"]
    assert found.signature == Span(1, 4)
    assert found.body == Span(5, 6)


def test_comment_and_blank_lines_after_the_colon_belong_to_the_body() -> None:
    code = "def f(a):\n    # note\n\n    return a\n"
    found = declarations(code)["f"]
    assert found.signature == Span(1, 1)
    assert found.body == Span(2, 4)


def test_one_line_def_has_no_body() -> None:
    found = declarations("def f(): return 1\n")["f"]
    assert found.signature == Span(1, 1)
    assert found.body is None


def test_multiline_signature_with_inline_body() -> None:
    found = declarations("def f(\n    a,\n): return a\n")["f"]
    assert found.signature == Span(1, 3)
    assert found.body is None


def test_variables() -> None:
    found = declarations("X = 1\nY: int = 2\n_Z = 3\nclass A:\n    attr = 1\n")
    assert found["X"].kind == "variable"
    assert found["X"].body is None
    assert found["Y"].is_public
    assert not found["_Z"].is_public
    assert found["A.attr"].kind == "variable"


def test_tuple_assignment_is_ignored() -> None:
    assert declarations("a, b = 1, 2\n") == {}


def test_async_def() -> None:
    found = declarations("async def f():\n    return 1\n")["f"]
    assert found.kind == "function"
    assert found.signature == Span(1, 1)
    assert found.body == Span(2, 2)


def test_parses_every_jigane_source_file() -> None:
    # tree-sitter 0.26.0 では、jigane 自身の行数の多いファイルで節点の位置が壊れて落ちた
    adapter = PythonAdapter()
    sources = sorted(Path(__file__).parent.parent.joinpath("jigane").rglob("*.py"))
    assert sources
    for source in sources:
        data = source.read_bytes()
        line_count = data.count(b"\n") + 1
        for declaration in adapter.declarations(parse(adapter, data).root_node, data):
            assert 1 <= declaration.span.start_line <= declaration.span.end_line <= line_count


def test_python_visibility_has_no_internal_level() -> None:
    found = declarations("def a(): ...\ndef _b(): ...\n")
    assert found["a"].visibility is Visibility.PUBLIC
    assert found["_b"].visibility is Visibility.FILE


def test_discriminator_branches_nest_elif_and_else() -> None:
    source = (
        b"def f(x):\n"
        b"    if x:\n"
        b"        return K.A\n"
        b"    elif y:\n"
        b"        raise ValueError\n"
        b"    else:\n"
        b"        return 1\n"
    )
    body = adapter.body_of(parse(adapter, source).root_node.children[0])
    assert body is not None
    steps = adapter.discriminator_branches(body, source)
    assert steps == [
        Branch(
            "x",
            (
                BranchArm("yes", (Return("K.A"),)),
                BranchArm(
                    "no",
                    (
                        Branch(
                            "y",
                            (BranchArm("yes", (Return(None),)), BranchArm("no", (Return(None),))),
                            None,
                        ),
                    ),
                ),
            ),
            None,
        )
    ]
