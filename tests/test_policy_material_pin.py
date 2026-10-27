from pathlib import Path

import pytest

from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import macro
from jigane.skills.assembly import AssemblyInput

FIXTURES = Path(__file__).parent / "fixtures" / "policy_material"

SOURCES = {
    "python": (
        "src/shop.py",
        (
            "import asyncio\nimport re\nimport subprocess\n\n\n"
            "class Shop:\n    def __init__(self) -> None:\n        self.items: list[str] = []\n\n"
            "    async def add(self, name: str) -> None:\n"
            "        await asyncio.sleep(0)\n"
            "        if re.match(r'\\d+', name):\n"
            "            self.items.append(name)\n\n"
            "    def run(self) -> None:\n"
            "        subprocess.run(['ls', '-l'])\n"
            "        open('data.txt')\n"
        ),
    ),
    "swift": (
        "src/Shop.swift",
        (
            "import Foundation\n\n"
            "final class Shop {\n    var items: [String] = []\n\n"
            "    func add(_ name: String) async {\n        items.append(name)\n    }\n\n"
            "    func load() {\n"
            '        _ = try? Data(contentsOf: URL(fileURLWithPath: "data.txt"))\n'
            '        let re = try? NSRegularExpression(pattern: "\\\\d+")\n'
            "        _ = re\n    }\n}\n"
        ),
    ),
    "csharp": (
        "src/Shop.cs",
        (
            "using System.Collections.Generic;\nusing System.Text.RegularExpressions;\n\n"
            "public class Shop\n{\n    private readonly List<string> items = new();\n\n"
            "    public void Add(string name)\n    {\n"
            '        if (Regex.IsMatch(name, @"\\d+")) { items.Add(name); }\n    }\n\n'
            "    public string Load()\n    {\n"
            '        return System.IO.File.ReadAllText("data.txt");\n    }\n}\n'
        ),
    ),
    "java": (
        "src/Shop.java",
        (
            "import java.util.ArrayList;\nimport java.util.List;\n"
            "import java.util.regex.Pattern;\n\n"
            "public class Shop {\n    private final List<String> items = new ArrayList<>();\n\n"
            "    public void add(String name) {\n"
            '        if (Pattern.matches("\\\\d+", name)) { items.add(name); }\n    }\n\n'
            "    public int count() {\n        return items.size();\n    }\n}\n"
        ),
    ),
}


@pytest.mark.parametrize("language", sorted(SOURCES))
def test_builtin_language_policy_material_text_is_pinned(
    language: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name, text = SOURCES[language]
    root = tmp_path / "shop"
    (root / name).parent.mkdir(parents=True)
    (root / name).write_text(text, encoding="utf-8")
    monkeypatch.chdir(root)
    review = create_feature_review(root, ["src"], "p")
    sections = macro.policy_sections(review)
    actual = macro.diagram_materials_text(AssemblyInput.load(review), sections)
    expected_file = FIXTURES / f"{language}.txt"
    assert actual == expected_file.read_text(encoding="utf-8")
