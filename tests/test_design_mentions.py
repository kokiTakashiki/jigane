from jigane.design.mentions import (
    PROSE_VIAS,
    Mention,
    MentionKind,
    MentionVia,
    extract_mentions,
)
from jigane.design.parse import parse_design

DOCUMENT = """\
# 題

## 1. 序論

`Review` と `review.Review` を使う。`kind` と `None` と `JSON` は名指しではない。
`parse_design()` も使う。
`src/app/store.py` と `notes.md` と `reviews.json` を読む。`jigane run --limit 3` を実行する。
`--json` だけの記述もある。`jigane` だけでは副コマンドにならない。

```text
`NotAMention` は柵の中
```

## 2. 設計

```mermaid
classDiagram
    class Cart
    class Item
    Cart --> Item
```

```mermaid
erDiagram
    ORDER ||--o{ LINE : has
```

```mermaid
sequenceDiagram
    participant C as Cart
    participant Store
    C->>Store: save
```

| 名前 | 説明 |
| --- | --- |
| `Pricing` | 表の中の名指し |

もう一度 `Review` を使う。
"""


def _mentions() -> tuple[Mention, ...]:
    return extract_mentions(parse_design(DOCUMENT))


def _names(kind: MentionKind) -> list[str]:
    return sorted({m.name for m in _mentions() if m.kind is kind})


def test_type_names_come_from_diagrams_and_code_spans() -> None:
    names = _names(MentionKind.TYPE)
    assert {"Cart", "Item", "Store", "Review", "parse_design", "ORDER", "LINE"} <= set(names)


def test_plain_words_and_fenced_text_are_not_mentions() -> None:
    names = {m.name for m in _mentions()}
    assert not names & {"kind", "None", "JSON", "NotAMention", "jigane"}


def test_qualified_names_and_paths() -> None:
    assert _names(MentionKind.QUALIFIED_NAME) == ["review.Review"]
    assert _names(MentionKind.PATH) == ["notes.md", "src/app/store.py"]


def test_subcommands_and_options() -> None:
    assert _names(MentionKind.SUBCOMMAND) == ["run"]
    assert _names(MentionKind.OPTION) == ["--json", "--limit"]


def test_stored_files() -> None:
    assert _names(MentionKind.STORED_FILE) == ["reviews.json"]


def test_same_name_is_kept_once_per_section() -> None:
    reviews = [m for m in _mentions() if m.name == "Review" and m.kind is MentionKind.TYPE]
    assert [m.section.title if m.section else None for m in reviews] == ["1. 序論", "2. 設計"]


def test_via_distinguishes_diagram_table_and_text() -> None:
    via = {m.name: m.via for m in _mentions()}
    assert via["Item"] is MentionVia.CLASS_DIAGRAM
    assert via["ORDER"] is MentionVia.ER_DIAGRAM
    assert via["Store"] is MentionVia.SEQUENCE_DIAGRAM
    assert via["Pricing"] is MentionVia.TABLE_CODE_SPAN
    assert via["parse_design"] is MentionVia.CODE_SPAN


def test_extraction_is_deterministic() -> None:
    document = parse_design(DOCUMENT)
    assert extract_mentions(document) == extract_mentions(document)


def test_sequence_aliases_pass_the_same_name_filter() -> None:
    names = {m.name for m in _mentions()}
    assert "C" not in names
    assert "Store" in names


PROSE_DOCUMENT = """\
# 題

## 1. 序論

地の文の scripts/sample_tool.py と、日本語に挟まれた（pyproject.toml）を読む。
語 and/or と foo.xyz は別。
`scripts/sample_tool.py` はコードスパンでも書く。https://example.com/a/b は URL。

| 項目 | 場所 |
| --- | --- |
| 試験 | tests/test_x.py |
"""


def test_prose_and_table_cell_paths_are_candidates() -> None:
    document = parse_design(PROSE_DOCUMENT)
    mentions = extract_mentions(document, path_suffixes=frozenset({"py", "toml"}))
    prose = {m.name: m for m in mentions if m.via in PROSE_VIAS}
    assert prose["tests/test_x.py"].via is MentionVia.TABLE_CELL
    assert prose["pyproject.toml"].via is MentionVia.PROSE
    assert "foo.xyz" not in prose
    assert not any("example.com" in m.name for m in mentions)


def test_code_span_path_wins_over_prose_in_same_section() -> None:
    document = parse_design(PROSE_DOCUMENT)
    mentions = extract_mentions(document, path_suffixes=frozenset({"py"}))
    eval_paths = [m for m in mentions if m.name == "scripts/sample_tool.py"]
    assert [m.via for m in eval_paths] == [MentionVia.CODE_SPAN]


SAME_NAME_DOCUMENT = """\
# 題

## 1. 序論

地の文の pyproject.toml と、コードスパンの `pyproject.toml` を同じ節に書く。

## 2. 設計

地の文だけの pyproject.toml。
"""


def test_same_name_in_prose_and_code_span_is_one_mention_with_code_span_kind() -> None:
    document = parse_design(SAME_NAME_DOCUMENT)
    mentions = extract_mentions(document, path_suffixes=frozenset({"toml"}))
    found = [(m.section.title if m.section else None, m.kind, m.via) for m in mentions]
    assert found == [
        ("1. 序論", MentionKind.STORED_FILE, MentionVia.CODE_SPAN),
        ("2. 設計", MentionKind.PATH, MentionVia.PROSE),
    ]


def test_code_span_that_is_no_kind_of_name_is_not_a_mention() -> None:
    document = parse_design("# 題\n\n## 1. 序論\n\n`two words` と `a, b` は名指しではない。\n")
    assert extract_mentions(document) == ()
