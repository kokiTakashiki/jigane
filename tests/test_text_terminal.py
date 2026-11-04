from jigane.text.terminal import sanitize_text


def test_sanitize_text_removes_terminal_controls_and_keeps_newline_and_tab() -> None:
    raw = "a\x1b[31mb\x1b]0;t\x07c\x08d\x9be\x7ff\tg\nh\x1bZi"
    assert sanitize_text(raw) == "abcdef\tg\nhi"
