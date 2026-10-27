from pathlib import Path

import pytest
from review_fakes import region_of, with_map

from jigane.judgment_map.region_table import Region
from jigane.policy_materials import declarations
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import policy_tree

PYTHON = (
    "LIMIT = 1\n\n\nclass Shelf:\n    size: int = 1\n\n"
    "    def area(self) -> int:\n        return 1\n"
)
SWIFT = (
    "let limit = 1\n"
    "struct Shelf {\n    var size: Int\n}\n"
    "extension Shelf {\n    var extra: Int { 1 }\n}\n"
    "enum Color {\n    case red, green\n}\n"
    "extension Other {\n    var value: Int { 1 }\n}\n"
)
JAVA = (
    "public class Shelf {\n    public int size;\n    public static final int LIMIT = 1;\n"
    "    public enum Color { RED, GREEN }\n    public int area() { return 1; }\n}\n"
)
CSHARP = (
    "namespace Shop {\npublic class Shelf {\n    public int Size;\n"
    "    public int Total { get; set; }\n    public enum Color { Red, Green }\n"
    "    public const int Limit = 1;\n    public int Area() { return 1; }\n}\n}\n"
)


def _review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, source: str) -> Review:
    root = tmp_path / "shop"
    root.mkdir()
    (root / name).write_text(source, encoding="utf-8")
    monkeypatch.chdir(root)
    review = create_feature_review(root, [name], "p")
    region = {
        "id": "J1",
        "key": f"{name}::::public_interface",
        "kind": "public_interface",
        "path": name,
        "qualified_name": "",
        "summary": "",
        "old_span": None,
        "new_span": {"start_line": 1, "end_line": len(source.splitlines())},
    }
    return with_map(review, {**review.map_kind.to_dict(), "regions": [region]})


def _row_names(review: Review) -> list[str]:
    tree = policy_tree.trees_of(review).after
    names: list[str] = []
    for region in review.map_kind.table.regions:
        file = tree.files.get(region.path)
        data = file.text.encode("utf-8") if file is not None else None
        names.extend(
            scoped.name
            for scoped in declarations.region_declarations(region, data)
            if scoped.role == "row"
        )
    return sorted(names)


def test_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = _review(tmp_path, monkeypatch, "shop.py", PYTHON)
    assert _row_names(review) == ["Shelf", "Shelf.area"]


def test_swift_extension_and_enum_case_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _review(tmp_path, monkeypatch, "Shop.swift", SWIFT)
    assert _row_names(review) == ["Color", "Shelf"]


def test_java_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = _review(tmp_path, monkeypatch, "Shelf.java", JAVA)
    assert _row_names(review) == ["Shelf", "Shelf.Color", "Shelf.area"]


def test_csharp_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review = _review(tmp_path, monkeypatch, "Shelf.cs", CSHARP)
    assert _row_names(review) == ["Shelf", "Shelf.Area", "Shelf.Color"]


def _region(name: str, path: str, new_span: dict[str, int] | None) -> Region:
    return region_of(
        {
            "id": "J1",
            "key": f"{path}::{name}::public_interface",
            "kind": "public_interface",
            "path": path,
            "qualified_name": name,
            "summary": "",
            "old_span": {"start_line": 1, "end_line": 3},
            "new_span": new_span,
        }
    )


def test_deleted_region_keeps_only_its_name() -> None:
    [named] = declarations.region_declarations(_region("Shelf.area", "shop.py", None), None)
    assert (named.name, named.declaration, named.role) == ("Shelf.area", None, "row")
    # 名前の無い箇所でも、宣言を挙げられないときは名前の無い一件を返す
    [unnamed] = declarations.region_declarations(_region("", "shop.py", None), b"x = 1\n")
    assert (unnamed.name, unnamed.declaration) == ("", None)
    [no_adapter] = declarations.region_declarations(
        _region("", "notes.unknownext", {"start_line": 1, "end_line": 2}), b"x"
    )
    assert no_adapter.declaration is None
