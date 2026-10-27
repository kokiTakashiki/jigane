from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "jigane"
# 副コマンドと表示の部品は、親の commands の README が扱う
COVERED_BY_PARENT = {PACKAGE / "commands" / "subcommands", PACKAGE / "commands" / "views"}


def test_every_package_has_a_readme() -> None:
    packages = [init.parent for init in PACKAGE.rglob("__init__.py")]
    missing = [
        str(package.relative_to(PACKAGE.parent))
        for package in packages
        if package not in COVERED_BY_PARENT and not (package / "README.md").is_file()
    ]
    assert missing == []
