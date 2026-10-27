from jigane.markdown.cells import cell, code_cell, markdown_table


def test_cell_escapes_pipe_and_flattens_newlines_but_keeps_backticks() -> None:
    assert cell("a|b\nc") == "a\\|b c"
    assert cell("`x`") == "`x`"


def test_code_cell_also_replaces_backticks() -> None:
    assert code_cell("a`b|c\nd") == "a'b\\|c d"


def test_markdown_table_renders_each_column_by_kind() -> None:
    text = markdown_table(
        ["a", "b", "c", "d"],
        [["x|y", "p|q", "r`s", "t`u"]],
        ["raw", "cell", "code", "codespan"],
    )
    assert text == "| a | b | c | d |\n| --- | --- | --- | --- |\n| x|y | p\\|q | r's | `t'u` |"


def test_markdown_table_caps_rows_and_names_the_rest() -> None:
    rows = [[str(n)] for n in range(5)]
    text = markdown_table(["n"], rows, ["raw"], cap=2, more="残り {rest} 件")
    assert text == "| n |\n| --- |\n| 0 |\n| 1 |\n\n残り 3 件"
    assert "残り" not in markdown_table(["n"], rows, ["raw"], cap=5, more="残り {rest} 件")
