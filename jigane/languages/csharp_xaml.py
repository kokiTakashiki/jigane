"""XAML のイベント結線から、C# の分離コードのメソッドの名前を読む。"""

import re

# 属性。名前空間の接頭辞（x:）と、添付の形（Button.Click）を名前に含める
_ATTRIBUTE = re.compile(r"""([A-Za-z_][\w:.\-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
# 型の情報が無いので、属性の名前だけでイベントかどうかを決める。表は WPF、WinForms 系の XAML、
# MAUI の主なイベント。名前の宣言に使う属性は、イベントの名前でないので表に入らない
_EVENT_ATTRIBUTES = frozenset(
    {
        "Click", "DoubleClick", "Clicked", "Pressed", "Released", "Tapped", "DoubleTapped",
        "Holding", "RightTapped", "Loaded", "Unloaded", "Initialized", "Appearing", "Disappearing",
        "Opened", "Opening", "Closed", "Closing", "Activated", "Deactivated", "Checked",
        "Unchecked", "Indeterminate", "Toggled", "Focused", "Unfocused", "GotFocus", "LostFocus",
        "Completed", "Navigated", "Navigating", "MouseDown", "MouseUp", "MouseMove", "MouseEnter",
        "MouseLeave", "MouseWheel", "MouseLeftButtonDown", "MouseLeftButtonUp",
        "MouseRightButtonDown", "MouseRightButtonUp", "KeyDown", "KeyUp", "TextInput",
        "DragEnter", "DragLeave", "DragOver", "Drop", "Drag", "Scroll", "Swiped", "Refreshing",
        "PointerPressed", "PointerReleased", "PointerMoved", "PointerEntered", "PointerExited",
        "ValueChanged", "SizeChanged",
    }
)  # fmt: skip
_EVENT_SUFFIX = "Changed"
_EVENT_PREFIX = "Preview"
_CODE_BEHIND_SUFFIX = ".xaml.cs"


def paired_xaml_path(path: str) -> str | None:
    """`Foo.xaml.cs` に対する `Foo.xaml`。分離コードの名前でなければ None。"""
    if not path.endswith(_CODE_BEHIND_SUFFIX):
        return None
    return path.removesuffix(".cs")


def _is_event_attribute(attribute: str) -> bool:
    # `Button.Click` と `x:Foo` の形は、最後の区切りより後ろの名前で見る
    local = re.split(r"[.:]", attribute)[-1]
    return (
        local in _EVENT_ATTRIBUTES
        or local.endswith(_EVENT_SUFFIX)
        or local.startswith(_EVENT_PREFIX)
    )


def handler_names(xaml: str) -> frozenset[str]:
    """XAML のイベントの属性のうち、値が識別子（`{...}` の拡張でない）ものの値を返す。

    `Click="CopyButton_Click"` の値が入る。`Text="Foo"` のようなイベントでない属性は入らない。
    分離コードに同名のメソッドがある入口に限って、呼び手の側で結線とみなす。
    """
    names: set[str] = set()
    for match in _ATTRIBUTE.finditer(_COMMENT.sub("", xaml)):
        attribute = match.group(1)
        value = match.group(2) if match.group(2) is not None else match.group(3)
        if not _is_event_attribute(attribute):
            continue
        if _IDENTIFIER.fullmatch(value):
            names.add(value)
    return frozenset(names)
