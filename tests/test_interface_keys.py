import re
from pathlib import Path

from jigane.languages.interface_keys import INTERFACE_KEYS
from jigane.skills.language_support import _interfaces

_ROOT = Path(__file__).parent.parent


def test_interface_keys_match_the_naming_rule() -> None:
    assert all(re.fullmatch(r"[a-z]+(_[a-z]+)*", key) for key in INTERFACE_KEYS.values())


def test_interface_keys_do_not_overlap() -> None:
    assert len(set(INTERFACE_KEYS.values())) == len(INTERFACE_KEYS)


def test_interface_keys_table_matches_the_published_capabilities() -> None:
    # _interfaces は capabilities が公開する能力の Protocol を列挙する。ずれたら落ちる
    assert set(_interfaces()) == set(INTERFACE_KEYS)


def _docs_rows() -> dict[str, str]:
    """作者向け文書の識別子の表。インターフェース名から識別子。"""
    text = (_ROOT / "docs" / "adding-languages.md").read_text(encoding="utf-8")
    section = text.split("## インターフェースの識別子", 1)[1].split("\n## ", 1)[0]
    return dict(re.findall(r"^\| `(\w+)` \| `(\w+)` \|$", section, re.MULTILINE))


def test_docs_table_matches_the_interface_keys() -> None:
    # 実際の languages list --json の interfaces のキーは、test_languages_command が表と照らす
    expected = {protocol.__name__: key for protocol, key in INTERFACE_KEYS.items()}
    assert _docs_rows() == expected


def test_release_notice_examples_exist_in_the_table() -> None:
    lines = (_ROOT / "docs" / "releases" / "v0.1.9.md").read_text(encoding="utf-8").splitlines()
    # 表の番号は版で変わるので、識別子を案内する段落を文で引く
    guide = next((line for line in lines if "`interfaces` のキーは" in line), None)
    assert guide is not None, "識別子を案内する文がお知らせにありません"
    examples = re.findall(
        r"`(added_public_names|discriminator_flow_steps|unsupported_syntax)`", guide
    )
    assert examples == ["added_public_names", "discriminator_flow_steps", "unsupported_syntax"]
    assert set(examples) <= set(INTERFACE_KEYS.values())
    link = next((line for line in lines if line.startswith("識別子の一覧は")), None)
    assert link is not None, "識別子の一覧へのリンクの行がお知らせにありません"
    assert "adding-languages.md#インターフェースの識別子" in link
