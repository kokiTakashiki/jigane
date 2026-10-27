from pathlib import Path

from jigane.languages.csharp import CSharpAdapter
from jigane.languages.csharp_xaml import handler_names
from jigane.sequence.diagram import EmptyEntryKind, EntryEvidence, build_diagrams
from jigane.sequence.index import build_index
from jigane.sequence.review_diagrams import sequences_of
from jigane.sequence.roots import key_of, select_roots

CODE = """
class Page {
    void CopyButton_Click(object sender, object e) { }
    void Helper() { }
}
"""

XAML = """<UserControl x:Class="App.Page" xmlns="http://x" xmlns:x="http://y">
  <!-- <Button Click="Commented_Click" /> -->
  <StackPanel x:Name="Helper" Tag="{Binding Helper}">
    <Button Content="Copy" Click="CopyButton_Click" />
  </StackPanel>
</UserControl>
"""


def _kinds(root: Path, code_path: str) -> dict[str, EmptyEntryKind]:
    adapter = CSharpAdapter()
    index = build_index(root, [code_path], adapter)
    changed = {key_of(m): m for m in index.callables.values()}
    selection = select_roots(index, changed, adapter)
    model = build_diagrams(index, selection, [])
    return {d["root"]: EntryEvidence.from_dict(d).kind() for d in model["diagrams"]}


def test_handler_wired_in_the_paired_xaml_is_not_declared_callless(tmp_path: Path) -> None:
    (tmp_path / "Page.xaml.cs").write_text(CODE, encoding="utf-8")
    (tmp_path / "Page.xaml").write_text(XAML, encoding="utf-8")
    kinds = _kinds(tmp_path, "Page.xaml.cs")
    assert kinds["Page.CopyButton_Click"] is EmptyEntryKind.MARKUP_CALLED
    # x:Name と {Binding} の値は結線ではない
    assert kinds["Page.Helper"] is EmptyEntryKind.NO_CALL


def test_without_a_paired_xaml_the_entry_stays_callless(tmp_path: Path) -> None:
    (tmp_path / "Page.xaml.cs").write_text(CODE, encoding="utf-8")
    assert _kinds(tmp_path, "Page.xaml.cs")["Page.CopyButton_Click"] is EmptyEntryKind.NO_CALL


def test_plain_cs_file_does_not_read_a_xaml(tmp_path: Path) -> None:
    (tmp_path / "Page.cs").write_text(CODE, encoding="utf-8")
    (tmp_path / "Page.xaml").write_text(XAML, encoding="utf-8")
    assert _kinds(tmp_path, "Page.cs")["Page.CopyButton_Click"] is EmptyEntryKind.NO_CALL


def test_attribute_value_that_is_not_a_method_name_is_ignored() -> None:
    xaml = '<Button Content="Copy it" Click="{x:Null}" Tag="12" Loaded=\'OnLoaded\' />'
    assert handler_names(xaml) == {"OnLoaded"}


def test_markup_called_entries_get_their_own_note() -> None:
    entry = {
        "root": "Page.CopyButton_Click",
        "path": "Page.xaml.cs",
        "line": 3,
        "arrows": [],
        "markup_called": True,
    }
    assert sequences_of({"diagrams": [entry]}, 1).notes[0].material == (
        "XAML から呼ばれる入口 1 件は図にしない: Page.CopyButton_Click"
    )


def test_non_event_attribute_with_a_method_name_is_not_a_wiring() -> None:
    xaml = (
        '<TextBlock Text="Foo" Tag="Bar" Button.Click="OnClick" '
        'TextChanged="OnChanged" PreviewKeyDown="OnKey" />'
    )
    assert handler_names(xaml) == {"OnClick", "OnChanged", "OnKey"}


def test_text_attribute_does_not_wire_a_same_named_method(tmp_path: Path) -> None:
    (tmp_path / "Page.xaml.cs").write_text("class Page { void Foo() { } }", encoding="utf-8")
    (tmp_path / "Page.xaml").write_text('<TextBlock Text="Foo" />', encoding="utf-8")
    assert _kinds(tmp_path, "Page.xaml.cs")["Page.Foo"] is EmptyEntryKind.NO_CALL
