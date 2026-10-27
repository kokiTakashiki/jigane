from pathlib import Path

from markdown_it import MarkdownIt

from jigane.design.mermaid import (
    ClassArrow,
    ClassDiagram,
    ClassRelationKind,
    DiagramKind,
    ErCardinality,
    ErDiagram,
    ErKey,
    FlowchartDiagram,
    FlowDirection,
    FlowLinkEnd,
    FlowLinkStyle,
    SequenceArrow,
    SequenceDiagram,
    UnreadableLine,
    UnreadableReason,
    UnsupportedDiagram,
    parse_diagram,
)
from jigane.design.parse import parse_design

ROOT = Path(__file__).resolve().parent.parent


def test_heading_tree_nests_and_ranges() -> None:
    text = "前置き\n# A\n## A1\n本文\n## A2\n# B\n"
    document = parse_design(text)
    a, b = document.sections
    assert (a.title, a.level, a.start_line, a.end_line) == ("A", 1, 2, 5)
    assert [c.title for c in a.children] == ["A1", "A2"]
    assert (a.children[0].start_line, a.children[0].end_line) == (3, 4)
    assert (b.start_line, b.end_line) == (6, 6)
    assert document.line_count == 6


def test_heading_in_fence_is_not_a_heading() -> None:
    text = "# A\n```\n# not\n```\n~~~~\n```\n## nested\n~~~~\n## B\n"
    document = parse_design(text)
    assert [s.title for s in document.all_sections()] == ["A", "B"]


def test_heading_level_skip_attaches_to_nearest_shallower() -> None:
    document = parse_design("# A\n#### D\n## B\n")
    a = document.sections[0]
    assert [c.title for c in a.children] == ["D", "B"]
    assert a.children[0].level == 4


def test_setext_is_not_a_heading() -> None:
    assert parse_design("題\n===\n本文\n").sections == ()


def test_closing_hashes_are_stripped_from_title() -> None:
    assert parse_design("## 題 ##\n").sections[0].title == "題"


CLASS = """classDiagram
    class Review {
        <<aggregate>>
        +str id
        +List~Finding~ findings
        count: int
        +run(x) Result
    }
    class Finding
    Review "1" *-- "*" Finding : has
    Finding <|-- Bug
    Review ..> Store
    Review : +str extra
    <<interface>> Store
    direction LR
    note for Review "x"
"""


def test_class_diagram() -> None:
    diagram = parse_diagram(CLASS)
    assert isinstance(diagram, ClassDiagram)
    assert diagram.kind is DiagramKind.CLASS
    types = {t.name: t for t in diagram.types}
    assert list(types) == ["Review", "Finding", "Bug", "Store"]
    review = types["Review"]
    assert review.stereotype == "aggregate"
    assert [(f.name, f.type) for f in review.fields] == [
        ("id", "str"),
        ("findings", "List~Finding~"),
        ("count", "int"),
        ("extra", "str"),
    ]
    assert [m.name for m in review.methods] == ["run"]
    assert types["Store"].stereotype == "interface"
    relations = [(r.left, r.right, r.kind, r.label) for r in diagram.relations]
    assert relations == [
        ("Review", "Finding", ClassRelationKind.COMPOSITION, "has"),
        ("Finding", "Bug", ClassRelationKind.INHERITANCE, None),
        ("Review", "Store", ClassRelationKind.DEPENDENCY, None),
    ]
    assert [r.arrow for r in diagram.relations] == [
        ClassArrow.COMPOSED_LEFT,
        ClassArrow.INHERITS_FROM,
        ClassArrow.DEPENDS_RIGHT,
    ]
    assert [u.text for u in diagram.unreadable] == ['    note for Review "x"']
    assert diagram.source == CLASS


ER = """erDiagram
    CUSTOMER ||--o{ ORDER : places
    ORDER }|..|{ ITEM : "contains"
    CUSTOMER {
        string name PK
        int age
        string email UK, FK "mail"
    }
    LONELY
    %% comment
    bad line here
"""


def test_er_diagram() -> None:
    diagram = parse_diagram(ER)
    assert isinstance(diagram, ErDiagram)
    entities = {e.name: e for e in diagram.entities}
    assert list(entities) == ["CUSTOMER", "ORDER", "ITEM", "LONELY"]
    attributes = entities["CUSTOMER"].attributes
    assert [(a.type, a.name, a.keys, a.comment) for a in attributes] == [
        ("string", "name", (ErKey.PRIMARY,), None),
        ("int", "age", (), None),
        ("string", "email", (ErKey.UNIQUE, ErKey.FOREIGN), "mail"),
    ]
    first, second = diagram.relations
    assert (first.left, first.right, first.left_cardinality, first.right_cardinality) == (
        "CUSTOMER",
        "ORDER",
        ErCardinality.EXACTLY_ONE,
        ErCardinality.ZERO_OR_MORE,
    )
    assert (second.left_cardinality, second.right_cardinality) == (
        ErCardinality.ONE_OR_MORE,
        ErCardinality.ONE_OR_MORE,
    )
    assert first.identifying
    assert first.label == "places"
    assert not second.identifying
    assert second.label == "contains"
    assert [u.text for u in diagram.unreadable] == ["    bad line here"]


SEQUENCE = """sequenceDiagram
    autonumber
    participant U as ユーザー
    actor CLI
    U->>+CLI: jigane run
    CLI-->>U: 結果
    loop 毎回
        CLI-)Store: 保存
    end
    Note over U,CLI: メモ
    U ?? CLI
"""


def test_sequence_diagram() -> None:
    diagram = parse_diagram(SEQUENCE)
    assert isinstance(diagram, SequenceDiagram)
    assert [(p.name, p.alias) for p in diagram.participants] == [
        ("U", "ユーザー"),
        ("CLI", None),
        ("Store", None),
    ]
    assert [(m.sender, m.receiver, m.text, m.arrow) for m in diagram.messages] == [
        ("U", "CLI", "jigane run", SequenceArrow.SOLID_ARROW),
        ("CLI", "U", "結果", SequenceArrow.DOTTED_ARROW),
        ("CLI", "Store", "保存", SequenceArrow.SOLID_ASYNC),
    ]
    assert [(u.line_number, u.text) for u in diagram.unreadable] == [(11, "    U ?? CLI")]


FLOWCHART = """flowchart LR
    A[入力<br/>ファイル] --> B{判定}
    B -->|はい| C("処理 (本体)")
    B -- いいえ --> D((終了))
    C --> D --> E
    subgraph S [群]
        F
    end
    A -.-> A2[[別]]
    style A fill:#fff
    X ==> ???
"""


def test_flowchart() -> None:
    diagram = parse_diagram(FLOWCHART)
    assert isinstance(diagram, FlowchartDiagram)
    nodes = {n.id: n.label for n in diagram.nodes}
    assert nodes == {
        "A": "入力\nファイル",
        "B": "判定",
        "C": "処理 (本体)",
        "D": "終了",
        "E": None,
        "F": None,
        "A2": "別",
    }
    assert [(e.source, e.target, e.label) for e in diagram.edges] == [
        ("A", "B", None),
        ("B", "C", "はい"),
        ("B", "D", "いいえ"),
        ("C", "D", None),
        ("D", "E", None),
        ("A", "A2", None),
    ]
    assert [(e.style, e.source_end, e.target_end) for e in diagram.edges] == [
        (FlowLinkStyle.SOLID, FlowLinkEnd.NONE, FlowLinkEnd.ARROW),
        (FlowLinkStyle.SOLID, FlowLinkEnd.NONE, FlowLinkEnd.ARROW),
        (FlowLinkStyle.SOLID, FlowLinkEnd.NONE, FlowLinkEnd.ARROW),
        (FlowLinkStyle.SOLID, FlowLinkEnd.NONE, FlowLinkEnd.ARROW),
        (FlowLinkStyle.SOLID, FlowLinkEnd.NONE, FlowLinkEnd.ARROW),
        (FlowLinkStyle.DOTTED, FlowLinkEnd.NONE, FlowLinkEnd.ARROW),
    ]
    assert [u.text for u in diagram.unreadable] == ["    X ==> ???"]


def test_flow_link_variants() -> None:
    diagram = parse_diagram("flowchart TD\n  A <--> B\n  B ==> C\n  C --o D\n  D --x E\n  E ~~~ F")
    assert isinstance(diagram, FlowchartDiagram)
    assert [(e.style, e.source_end, e.target_end) for e in diagram.edges] == [
        (FlowLinkStyle.SOLID, FlowLinkEnd.ARROW, FlowLinkEnd.ARROW),
        (FlowLinkStyle.THICK, FlowLinkEnd.NONE, FlowLinkEnd.ARROW),
        (FlowLinkStyle.SOLID, FlowLinkEnd.NONE, FlowLinkEnd.CIRCLE),
        (FlowLinkStyle.SOLID, FlowLinkEnd.NONE, FlowLinkEnd.CROSS),
        (FlowLinkStyle.INVISIBLE, FlowLinkEnd.NONE, FlowLinkEnd.NONE),
    ]


def test_unsupported_diagram_keeps_source() -> None:
    source = "stateDiagram-v2\n  [*] --> A"
    diagram = parse_diagram(source)
    assert isinstance(diagram, UnsupportedDiagram)
    assert diagram.declared_type == "stateDiagram-v2"
    assert diagram.source == source


def test_front_matter_is_skipped() -> None:
    diagram = parse_diagram("---\ntitle: x\n---\nflowchart TD\n  A --> B")
    assert isinstance(diagram, FlowchartDiagram)
    assert len(diagram.edges) == 1


def test_table_with_escape_and_section() -> None:
    text = "# 節\n\n| 名前 | 値 |\n| :-- | --: |\n| a \\| b | 1 |\n| c |\n\n後ろ\n"
    document = parse_design(text)
    (table,) = document.tables
    assert table.columns == ("名前", "値")
    assert table.rows == (("a | b", "1"), ("c", ""))
    assert (table.start_line, table.end_line) == (3, 6)
    assert table.section is document.sections[0]


def test_table_requires_delimiter_and_skips_fences() -> None:
    text = "a | b\nc | d\n\n```\n| x | y |\n| - | - |\n```\n"
    assert parse_design(text).tables == ()


def test_diagram_belongs_to_deepest_section_and_has_lines() -> None:
    text = "# A\n## B\n  ```mermaid\n  flowchart TD\n    X --> Y\n  ```\n"
    document = parse_design(text)
    (block,) = document.diagrams
    assert block.section is document.sections[0].children[0]
    assert (block.start_line, block.end_line) == (3, 6)
    assert isinstance(block.diagram, FlowchartDiagram)
    assert len(block.diagram.edges) == 1


def test_unclosed_mermaid_fence_reads_to_end() -> None:
    document = parse_design("```mermaid\nflowchart TD\n  A --> B\n")
    assert len(document.diagrams[0].diagram.source.split("\n")) == 2


def _independent_counts(path: Path) -> tuple[int, list[str]]:
    tokens = MarkdownIt("commonmark").parse(path.read_text(encoding="utf-8"))
    headings = sum(token.type == "heading_open" for token in tokens)
    kinds = [
        token.content.split()[0]
        for token in tokens
        if token.type == "fence" and token.info.strip() == "mermaid" and token.content.strip()
    ]
    return headings, kinds


def test_sample_documents_match_independent_counts() -> None:
    by_header = {"classDiagram": "class", "erDiagram": "er", "sequenceDiagram": "sequence"}
    for name in ("review-store.md", "diagram-kinds.md"):
        path = ROOT / "tests" / "fixtures" / "design" / name
        document = parse_design(path.read_text(encoding="utf-8"))
        headings, kinds = _independent_counts(path)
        assert len(document.all_sections()) == headings
        assert [block.diagram.kind.value for block in document.diagrams] == [
            by_header.get(kind, "flowchart" if kind in ("flowchart", "graph") else "unsupported")
            for kind in kinds
        ]
        assert all(block.section is not None for block in document.diagrams)


def test_document_keeps_unreadable_spots_with_reasons() -> None:
    text = (
        "| a | b |\n| - |\n\n"
        "| a | b |\n| - | - |\n| 1 |\n| 1 | 2 | 3 |\n\n"
        "```mermaid\nflowchart TD\n  A --> B\n  ??\n"
    )
    document = parse_design(text)
    assert [(u.line_number, u.text, u.reason) for u in document.unreadable] == [
        (1, "| a | b |", UnreadableReason.TABLE_DELIMITER_MISMATCH),
        (6, "| 1 |", UnreadableReason.TABLE_ROW_MISMATCH),
        (7, "| 1 | 2 | 3 |", UnreadableReason.TABLE_ROW_MISMATCH),
        (9, "```mermaid", UnreadableReason.UNCLOSED_FENCE),
    ]
    assert [(u.line_number, u.text) for u in document.all_unreadable()][-1] == (12, "  ??")
    assert len(document.all_unreadable()) == 5


def test_well_formed_document_has_nothing_unreadable() -> None:
    document = parse_design("| a |\n| - |\n| 1 |\n\n```mermaid\nflowchart TD\n  A --> B\n```\n")
    assert document.all_unreadable() == ()


def test_flowchart_header_direction_and_trailing_statement() -> None:
    diagram = parse_diagram("graph TD; A-->B")
    assert isinstance(diagram, FlowchartDiagram)
    assert diagram.direction is FlowDirection.TOP_DOWN
    assert [(e.source, e.target) for e in diagram.edges] == [("A", "B")]
    assert diagram.unreadable == ()
    plain = parse_diagram("flowchart LR\n  A --> B")
    assert isinstance(plain, FlowchartDiagram)
    assert plain.direction is FlowDirection.LEFT_RIGHT
    assert parse_diagram("graph\n  A --> B").direction is None  # type: ignore[union-attr]


def test_flowchart_header_trailing_unreadable_is_kept() -> None:
    diagram = parse_diagram("flowchart TD ???\n  A --> B", first_line=5)
    assert isinstance(diagram, FlowchartDiagram)
    assert diagram.unreadable == (
        UnreadableLine(5, "flowchart TD ???", UnreadableReason.UNRECOGNIZED_AFTER_HEADER),
    )


def test_unreadable_text_is_the_raw_line_with_indent() -> None:
    diagram = parse_diagram("flowchart TD\n    A --> B\n\t ???\n", first_line=2)
    assert [(u.line_number, u.text) for u in diagram.unreadable] == [(4, "\t ???")]


def test_header_continuation_unreadable_keeps_whole_raw_line_with_reason() -> None:
    diagram = parse_diagram("graph TD; ???", first_line=1)
    assert diagram.unreadable == (
        UnreadableLine(1, "graph TD; ???", UnreadableReason.UNRECOGNIZED_AFTER_HEADER),
    )


def test_unclosed_brace_keeps_original_line() -> None:
    diagram = parse_diagram("classDiagram\n    class Review~T~ {\n        +int a", first_line=3)
    assert [(u.line_number, u.text, u.reason) for u in diagram.unreadable] == [
        (4, "    class Review~T~ {", UnreadableReason.UNCLOSED_BRACE)
    ]
    er = parse_diagram("erDiagram\n  A {\n    int x")
    assert er.unreadable == (UnreadableLine(2, "  A {", UnreadableReason.UNCLOSED_BRACE),)


def test_unclosed_front_matter_keeps_following_lines() -> None:
    diagram = parse_diagram("---\ntitle: x\nflowchart TD\n  A --> B", first_line=10)
    assert isinstance(diagram, UnsupportedDiagram)
    assert [(u.line_number, u.text.strip(), u.reason) for u in diagram.unreadable] == [
        (11, "title: x", UnreadableReason.UNCLOSED_FRONT_MATTER),
        (12, "flowchart TD", UnreadableReason.UNCLOSED_FRONT_MATTER),
        (13, "A --> B", UnreadableReason.UNCLOSED_FRONT_MATTER),
    ]


def test_flowchart_reserved_word_as_node_name_is_read_as_element() -> None:
    diagram = parse_diagram("flowchart TD\n  class --> B\n  end --> C\n  click -- t --> D")
    assert isinstance(diagram, FlowchartDiagram)
    assert [(e.source, e.target) for e in diagram.edges] == [
        ("class", "B"),
        ("end", "C"),
        ("click", "D"),
    ]
    assert diagram.unreadable == ()


def test_flowchart_non_element_lines_are_skipped() -> None:
    source = (
        "flowchart TD\n  subgraph S [t]\n  direction LR\n  A --> B\n  end\n"
        "  style A fill:#fff\n  class A,B cls\n  classDef cls fill:#f00\n"
        "  linkStyle 0 stroke:red\n  click A callback"
    )
    diagram = parse_diagram(source)
    assert isinstance(diagram, FlowchartDiagram)
    assert [(e.source, e.target) for e in diagram.edges] == [("A", "B")]
    assert diagram.unreadable == ()


def test_sequence_sender_name_containing_cross_arrow_text_is_not_split() -> None:
    diagram = parse_diagram("sequenceDiagram\n  A-xyz->>B: hi\n")
    assert isinstance(diagram, SequenceDiagram)
    assert [(m.sender, m.receiver, m.arrow, m.text) for m in diagram.messages] == [
        ("A-xyz", "B", SequenceArrow.SOLID_ARROW, "hi")
    ]
    assert diagram.unreadable == ()


def test_sequence_short_arrows_are_still_read() -> None:
    diagram = parse_diagram("sequenceDiagram\n  A-xB: hi\n  A-)B: async\n  A-->>B: back\n")
    assert isinstance(diagram, SequenceDiagram)
    assert [(m.sender, m.receiver, m.arrow.value) for m in diagram.messages] == [
        ("A", "B", "-x"),
        ("A", "B", "-)"),
        ("A", "B", "-->>"),
    ]


def test_er_entity_name_with_hyphen_or_dot_is_read_in_all_positions() -> None:
    source = (
        "erDiagram\n  ORDER-ITEM\n  v1.USER {\n    int id PK\n  }\n"
        "  ORDER-ITEM }o--|| v1.USER : of\n"
    )
    diagram = parse_diagram(source)
    assert isinstance(diagram, ErDiagram)
    assert [e.name for e in diagram.entities] == ["ORDER-ITEM", "v1.USER"]
    assert len(diagram.relations) == 1
    assert diagram.unreadable == ()


def test_section_at_returns_deepest_section_or_none() -> None:
    document = parse_design("前置き\n# 題\n\n## 1. 序論\n\n本文\n")
    assert document.section_at(1) is None
    deepest = document.section_at(6)
    assert deepest is not None
    assert deepest.title == "1. 序論"
    outer = document.section_at(3)
    assert outer is not None
    assert outer.title == "題"
