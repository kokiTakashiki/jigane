from jigane.markdown.blocks import Fence, Heading, SectionSpan, fences, headings, section_spans


def test_closed_fence_has_info_and_body() -> None:
    found = fences(["a", "```mermaid x", "body", "```", "b"])
    assert found == [Fence(2, 4, closed=True, info="mermaid x")]
    assert list(found[0].body_lines) == [3]


def test_unclosed_fence_runs_to_last_line() -> None:
    found = fences(["```", "a", "b"])
    assert found == [Fence(1, 3, closed=False, info="")]
    assert list(found[0].body_lines) == [2, 3]


def test_closing_fence_needs_same_char_and_enough_length() -> None:
    assert fences(["````", "```", "~~~~", "````"]) == [Fence(1, 4, closed=True, info="")]


def test_headings_carry_level_and_title_and_skip_fences() -> None:
    lines = ["# A ##", "```", "# no", "```", "### B  "]
    assert headings(lines) == [Heading(1, 1, "A"), Heading(5, 3, "B")]


def test_section_span_runs_to_next_heading_of_same_or_shallower_level() -> None:
    lines = ["# A", "a", "## B", "b", "### C", "c", "## D", "d", "# E", "e"]
    spans = section_spans(lines)
    assert [(s.heading.title, s.start_line, s.end_line) for s in spans] == [
        ("A", 1, 8),
        ("B", 3, 6),
        ("C", 5, 6),
        ("D", 7, 8),
        ("E", 9, 10),
    ]
    assert spans[0] == SectionSpan(Heading(1, 1, "A"), 1, 8)


def test_section_span_ignores_hash_in_fence_and_last_runs_to_end() -> None:
    lines = ["# A", "```", "# not", "```", "## B", "x"]
    spans = section_spans(lines)
    assert [(s.heading.title, s.end_line) for s in spans] == [("A", 6), ("B", 6)]
