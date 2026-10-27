import re

from jigane.publishing.rendering import escape_markdown

HOSTILE_BODY = (
    "outputs['rebuttal'][i] と __all__ と *x* と <tag> と `code` と # と | と & と ~~z~~\n"
    "- 入れ子に見える行\n"
    "1. 番号に見える行\n"
    "---\n"
    "# 見出しに見える行"
)


def test_escape_is_not_doubled() -> None:
    once = escape_markdown(HOSTILE_BODY)
    assert escape_markdown(once) == once


def test_escape_only_adds_backslashes() -> None:
    escaped = escape_markdown(HOSTILE_BODY)
    removed = re.sub(r"\\(.)", r"\1", escaped)
    assert removed == HOSTILE_BODY


def test_plain_text_is_unchanged() -> None:
    plain = "命名の揺れ (a, b): x.y 1.5 版"
    assert escape_markdown(plain) == plain


def test_line_start_markers_are_escaped() -> None:
    escaped = escape_markdown("- a\n  + b\n2) c\n1.5 d\n===")
    assert escaped.splitlines() == ["\\- a", "  \\+ b", "2\\) c", "1.5 d", "\\==="]
