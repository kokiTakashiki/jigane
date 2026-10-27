from pathlib import Path

import jigane

OLD_NAMES = ("外観", "細部")
TEXT_SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".txt", ".json"}
# 群の名前は方針、実装、セキュリティに改めた。旧称を意図して残す場所はここへ理由とともに足す
ALLOWED: dict[str, str] = {}


def test_old_group_names_do_not_remain_in_human_facing_strings() -> None:
    package_root = Path(jigane.__file__).parent
    remaining: list[str] = []
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(package_root).as_posix()
        if relative in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        remaining.extend(f"{relative}: {name}" for name in OLD_NAMES if name in text)
    assert remaining == []
