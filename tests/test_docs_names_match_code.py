"""プラグインの作者向けの文書が書き写した名前と警告の id が、コードの定義と一致することの試験。"""

import re
from pathlib import Path

import pytest

from jigane.gates.construct_kind import ConstructKind
from jigane.languages import api
from jigane.languages.api import capabilities

_ROOT = Path(__file__).parent.parent
_AUTHOR_DOCS = (
    _ROOT / "docs" / "adding-languages.md",
    _ROOT / "examples" / "jigane-language-kotlin" / "README.md",
)
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_CAMEL_CASE_NAME = re.compile(r"[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*")
# 例のパッケージが定義する名前（KotlinAdapter）と Python 組み込みの例外（ValueError）で、
# jigane の公開の名前ではない
_NOT_PUBLISHED_NAMES = {"KotlinAdapter", "ValueError"}


# 改名の節は、旧名を載せるのが目的なので、公開の名前かの検査から外す。
# 節の新名が公開されていることは tests/test_adding_languages_rename_section.py が確かめる
_RENAME_SECTION = re.compile(
    r"^## 言語プラグインのインターフェースの改名\n.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL
)


def _spans(path: Path) -> list[str]:
    return _CODE_SPAN.findall(path.read_text(encoding="utf-8"))


def _spans_outside_rename_section(path: Path) -> list[str]:
    return _CODE_SPAN.findall(_RENAME_SECTION.sub("", path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", _AUTHOR_DOCS, ids=lambda path: path.name)
def test_interface_and_value_type_names_in_docs_are_published(path: Path) -> None:
    published = set(capabilities.__all__) | set(api.__all__)
    names = {
        span for span in _spans_outside_rename_section(path) if _CAMEL_CASE_NAME.fullmatch(span)
    }
    assert names - _NOT_PUBLISHED_NAMES <= published


def test_warning_ids_in_docs_match_the_construct_kinds() -> None:
    expected = {
        ConstructKind.SYNTAX_ERROR.entry_id,
        ConstructKind.of_language("swift", "operator_function").entry_id,
        ConstructKind.of_language("csharp", "nameless_declarator").entry_id,
        # 文書の架空の例
        ConstructKind.of_language("kotlin", "foo").entry_id,
    }
    docs = (_ROOT / "docs" / "commands.md", _ROOT / "docs" / "adding-languages.md")
    # `UNREADABLE-<言語名>-<種類>` の形の記述は大文字で終わらないので拾わない
    found = {
        entry_id
        for path in docs
        for span in _spans(path)
        for entry_id in re.findall(r"UNREADABLE-[A-Z][A-Z-]*", span)
    }
    assert found == expected
