from pathlib import Path

from jigane.figures.models import (
    PATH_TREE_INTRO,
    FileState,
    NoteText,
    Panel,
    TreeFigure,
)
from jigane.policy_materials.changed_files import (
    FileChange,
    current_tree_figure,
    file_summaries,
    tree_mermaid,
)
from jigane.policy_materials.current_shape import (
    NO_NAMED_CLASSES_TEXT,
    NO_NAMED_ENTRIES_TEXT,
    current_shape,
)

PYTHON_SHOP = """\
class Item:
    name: str


class Cart:
    items: list[Item]
    owner: "Owner"

    def add(self, item: Item) -> None:
        self.items.append(item)

    def checkout(self) -> int:
        return Pricing().total(self)


class Pricing:
    def total(self, cart: Cart) -> int:
        return len(cart.items)


class Owner:
    label: str


class Unrelated:
    secret: int
"""

JAVA_ORDER = """\
package shop;

class Line {
    int count;
}

class Order {
    Line first;

    int sum() {
        return new Tax().apply(this);
    }
}

class Tax {
    int apply(Order order) {
        return order.first.count;
    }
}

class Stray {
    int hidden;
}
"""


def _write(root: Path) -> list[str]:
    (root / "pkg").mkdir()
    (root / "pkg" / "shop.py").write_text(PYTHON_SHOP, encoding="utf-8")
    (root / "shop").mkdir()
    (root / "shop" / "Order.java").write_text(JAVA_ORDER, encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "readme.md").write_text("# readme\n", encoding="utf-8")
    return ["pkg/shop.py", "shop/Order.java", "docs/readme.md"]


NAMES = ["pkg.shop.Cart", "pkg.shop.Cart.checkout", "Order", "Order.sum"]


def test_three_figures_from_named_types_and_entries(tmp_path: Path) -> None:
    paths = _write(tmp_path)

    shape = current_shape(tmp_path, paths, NAMES)

    classes = shape.classes.panels[0].mermaid
    # 名を挙げた型と、その項目の型として現れる型が出る
    assert "class Cart[" in classes
    assert "class Item[" in classes
    assert "class Owner[" in classes
    assert "class Order[" in classes
    assert "class Line[" in classes
    assert "Cart --> Item" in classes
    flows = {flow.title.split("（")[0]: flow for flow in shape.sequences.flows}
    assert set(flows) == {"pkg.shop.Cart.checkout", "Order.sum"}
    assert "P0->>P1: total()" in flows["pkg.shop.Cart.checkout"].panels[0].mermaid
    assert "apply()" in flows["Order.sum"].panels[0].mermaid


def test_unnamed_types_are_absent(tmp_path: Path) -> None:
    paths = _write(tmp_path)

    shape = current_shape(tmp_path, paths, NAMES)

    classes = shape.classes.panels[0].mermaid
    for unnamed in ("Unrelated", "Stray", "Pricing", "Tax"):
        assert f"class {unnamed}[" not in classes
    # 名を挙げていない入口は図にならない
    titles = [flow.title for flow in shape.sequences.flows]
    assert not any("Pricing.total" in title or "Tax.apply" in title for title in titles)


def test_markdown_file_appears_only_in_the_tree(tmp_path: Path) -> None:
    paths = _write(tmp_path)

    shape = current_shape(tmp_path, paths, NAMES)

    assert "readme.md" in shape.tree.after.mermaid
    assert "shop.py" in shape.tree.after.mermaid
    # 変更の印は付かない
    assert "変更" not in shape.tree.after.mermaid
    assert "classDef" not in shape.tree.after.mermaid
    assert "readme" not in shape.classes.text()
    assert "readme" not in shape.sequences.text()


def test_unselected_file_is_not_drawn(tmp_path: Path) -> None:
    _write(tmp_path)

    shape = current_shape(tmp_path, ["pkg/shop.py"], NAMES)

    assert "Order.java" not in shape.tree.after.mermaid
    assert "class Order[" not in shape.classes.panels[0].mermaid
    assert [f.title.split("（")[0] for f in shape.sequences.flows] == ["pkg.shop.Cart.checkout"]


def test_unmatched_names_give_empty_figures_with_reason(tmp_path: Path) -> None:
    paths = _write(tmp_path)

    shape = current_shape(tmp_path, paths, ["no.such.Type"])

    assert shape.classes.panels == ()
    assert shape.classes.description == NO_NAMED_CLASSES_TEXT
    assert shape.sequences.flows == ()
    assert shape.sequences.notes == (NoteText(NO_NAMED_ENTRIES_TEXT, NO_NAMED_ENTRIES_TEXT),)
    assert shape.tree.is_drawable()


def test_current_tree_text_lists_paths_without_change_totals() -> None:
    figure = current_tree_figure(["pkg/a.py", "docs/b.md"])
    text = figure.text()
    assert text.startswith(PATH_TREE_INTRO)
    assert "- docs/b.md" in text
    assert "- pkg/a.py" in text
    assert "変更の合計" not in text


def test_mixed_tree_keeps_change_totals_and_unmarked_unchanged_leaf() -> None:
    files = {
        "pkg/a.py": FileChange(FileState.CHANGED, 3),
        "pkg/b.py": FileChange(FileState.UNCHANGED, 0),
    }
    figure = TreeFigure(Panel("木", tree_mermaid(files)), file_summaries(files))
    text = figure.text()
    assert "変更の合計は 2 ファイル" in text
    assert '["b.py"]' in figure.after.mermaid
