from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jigane.technology_list.bridges import collect, load_table

_SWIFT = """import SwiftUI

struct Web: UIViewRepresentable, Equatable {
    let url: URL
    func makeUIView(context: Context) -> WKWebView {
        let view = WKWebView()
        view.load(URLRequest(url: url))
        return view
    }
    func updateUIView(_ view: WKWebView, context: Context) {}
}

final class Host: UIHostingController<Root> {}

final class Router {
    func show() {
        let host = UIHostingController(rootView: Root())
    }
}
"""


def _collect(tmp_path: Path, files: dict[str, str]) -> list[dict[str, Any]]:
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    review = SimpleNamespace(repository_root=str(tmp_path), paths=list(files))
    return collect(review)  # type: ignore[arg-type]


def test_swift_representable_counts_members_and_body_lines(tmp_path: Path) -> None:
    found = _collect(tmp_path, {"a.swift": _SWIFT})
    web = found[0]
    assert (web["name"], web["bridge"], web["via"], web["line"]) == (
        "Web",
        "UIViewRepresentable",
        "base",
        3,
    )
    assert (web["from"], web["to"]) == ("SwiftUI", "UIKit")
    assert web["members"] == 3
    assert web["type_lines"] == 9
    assert web["body_lines"] == {"makeUIView": 5, "updateUIView": 1}


def test_swift_hosting_controller_inheritance_and_creation(tmp_path: Path) -> None:
    found = _collect(tmp_path, {"a.swift": _SWIFT})
    host, router = found[1], found[2]
    assert (host["name"], host["bridge"], host["via"]) == ("Host", "UIHostingController", "base")
    assert host["body_lines"] is None
    assert (router["name"], router["via"], router["line"]) == ("Router", "creation", 17)
    assert (router["from"], router["to"]) == ("UIKit", "SwiftUI")
    assert router["members"] == 1


def test_csharp_dllimport_and_libraryimport(tmp_path: Path) -> None:
    text = (
        "class A {\n"
        '    [DllImport("x.dll")]\n'
        "    static extern int F(int a);\n"
        '    [System.Runtime.InteropServices.LibraryImport("y")]\n'
        "    static partial int G();\n"
        "    [Obsolete]\n"
        "    static int H() { return 1; }\n"
        "}\n"
    )
    found = _collect(tmp_path, {"a.cs": text})
    assert [(b["name"], b["bridge"], b["via"], b["line"]) for b in found] == [
        ("F", "DllImport", "attribute", 2),
        ("G", "LibraryImport", "attribute", 4),
    ]
    assert found[0]["to"] == "ネイティブ"


def test_java_native_method(tmp_path: Path) -> None:
    text = "class J {\n    public native int f(int a);\n    public int g() { return 1; }\n}\n"
    found = _collect(tmp_path, {"J.java": text})
    assert [(b["name"], b["bridge"], b["via"], b["line"]) for b in found] == [
        ("f", "native", "modifier", 2)
    ]


def test_python_native_imports(tmp_path: Path) -> None:
    text = "import ctypes\nfrom cffi import FFI\nimport os\nfrom . import ctypes_helper\n"
    found = _collect(tmp_path, {"a.py": text})
    assert [(b["bridge"], b["via"], b["line"]) for b in found] == [
        ("ctypes", "import", 1),
        ("cffi", "import", 2),
    ]


def test_tables_exist_for_each_language() -> None:
    assert {"UIViewRepresentable", "NSHostingView"} <= set(load_table("swift"))
    assert set(load_table("csharp")) == {"DllImport", "LibraryImport"}
    assert set(load_table("java")) == {"native"}
    assert set(load_table("python")) == {"ctypes", "cffi"}
