from jigane.languages.base import Locals, parse
from jigane.languages.swift import SwiftAdapter
from jigane.preconditions.required import required

adapter = SwiftAdapter()

SOURCE = b"""
func f(a: Int) {
  let x: Foo = Foo()
  var y = Bar.init(z: 1)
  let (p, q) = (1, 2)
  let z = Array<Int>()
  for item in items { item.go() }
  for case let k as Foo in items {}
  if let w = opt, var v: Foo = other { w.go() }
  guard let g = opt else { return }
  while let n = next() { }
  let c = { (u: Foo, t) in u.go() }
  items.map { m in m.go() }
  func inner(_ r: Foo) {}
  switch s { case .a(let e): break
  default: break }
}
"""


def _locals() -> Locals:
    function = parse(adapter, SOURCE).root_node.children[0]
    return adapter.locals_of(required(function.child_by_field_name("body")))


def test_variables_carry_annotation_and_initializer() -> None:
    found = {v.name: v for v in _locals().variables}
    assert found["x"].type_annotation == "Foo"
    assert found["y"].type_annotation is None
    assert found["y"].initializer is not None
    assert found["z"].initializer is not None
    assert found["z"].initializer.type == "constructor_expression"


def test_tuple_binding_has_neither_annotation_nor_initializer() -> None:
    found = {v.name: v for v in _locals().variables}
    assert (found["p"].type_annotation, found["p"].initializer) == (None, None)
    assert "q" in found


def test_loop_and_condition_bindings() -> None:
    found = {v.name: v for v in _locals().variables}
    assert found["item"].type_annotation is None
    assert found["k"].type_annotation == "Foo"
    assert found["v"].type_annotation == "Foo"
    assert {"w", "g", "n", "e"} <= set(found)
    assert found["n"].initializer is not None


def test_closure_and_local_function_parameters() -> None:
    result = _locals()
    found = {v.name: v for v in result.variables}
    assert found["u"].type_annotation == "Foo"
    assert found["r"].type_annotation == "Foo"
    assert result.lambda_parameters == frozenset({"t", "m"})
    assert result.functions == frozenset({"inner"})
