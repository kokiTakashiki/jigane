"""作者向けの文書の改名の節が、公開のインターフェースと案内先の URL の綴りに合う試験。"""

import dataclasses
import re
from pathlib import Path
from typing import Protocol

from jigane.commands.language_guidance import guide_url
from jigane.languages.api import capabilities

_ROOT = Path(__file__).parent.parent
_HEADING = "言語プラグインのインターフェースの改名"
_CODE = re.compile(r"`([^`]+)`")


def _subsection(title: str) -> list[str]:
    lines = (_ROOT / "docs" / "adding-languages.md").read_text(encoding="utf-8").splitlines()
    start = lines.index(f"## {_HEADING}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    section = lines[start + 1 : end]
    begin = section.index(f"### {title}")
    body: list[str] = []
    for line in section[begin + 1 :]:
        if line.startswith("### "):
            break
        body.append(line)
    return body


def _new_names(title: str) -> list[str]:
    """表の二列目（新名）の `` で囲んだ名前。旧名の列は読まない。"""
    names: list[str] = []
    for row in _subsection(title):
        cells = row.split("|")
        if len(cells) < 4 or cells[1].strip() in {"旧名", "型", "---"} or set(row) <= set("|- "):
            continue
        names.extend(_CODE.findall(cells[2]))
    return names


def _protocol_members() -> set[str]:
    members: set[str] = set()
    for value in vars(capabilities).values():
        if isinstance(value, type) and Protocol in value.__mro__ and value is not Protocol:
            members.update(vars(value))
    return members


def test_guide_url_anchor_matches_the_section_heading() -> None:
    assert guide_url().split("#", 1)[1] == _HEADING


def test_every_new_interface_name_is_exported_from_capabilities() -> None:
    names = _new_names("インターフェース")
    assert names
    assert [n for n in names if not hasattr(capabilities, n)] == []


def test_every_new_method_name_is_a_member_of_a_public_protocol() -> None:
    names = _new_names("メソッド")
    assert names
    assert [n for n in names if n not in _protocol_members()] == []


def test_every_new_value_type_is_exported_from_capabilities() -> None:
    names = _new_names("値の型")
    assert names
    assert [n for n in names if not hasattr(capabilities, n)] == []


def _field_rows() -> list[tuple[list[str], list[str]]]:
    """フィールドの表の行。型の列の名前と、`旧 → 新` の新の名前を返す。"""
    rows: list[tuple[list[str], list[str]]] = []
    for row in _subsection("フィールド"):
        cells = row.split("|")
        if len(cells) < 4 or cells[1].strip() in {"型", "---"} or set(row) <= set("|- "):
            continue
        new_names = [
            pair.split("→")[1].strip().strip("`") for pair in cells[2].split("、") if "→" in pair
        ]
        rows.append((_CODE.findall(cells[1]), new_names))
    return rows


def test_every_new_field_name_exists_on_its_value_types() -> None:
    rows = _field_rows()
    assert rows
    missing: list[str] = []
    for types, new_names in rows:
        for type_name in types:
            value = getattr(capabilities, type_name)
            members = (
                {f.name for f in dataclasses.fields(value)}
                if dataclasses.is_dataclass(value)
                else set()
            )
            members |= set(vars(value))
            missing.extend(f"{type_name}.{n}" for n in new_names if n not in members)
    assert missing == []
