import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

from jigane.groups.definitions import GroupId
from jigane.llm_runs.claude import REVIEWER_STRIPPED_ENVIRONMENT_VARIABLES
from jigane.llm_runs.claude_inputs import Channel, ClaudeInput, Role

ROOT = Path(__file__).parent.parent


def load_generator() -> ModuleType:
    path = ROOT / "scripts" / "generate_claude_inputs_table.py"
    spec = importlib.util.spec_from_file_location("generate_claude_inputs_table", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_security() -> str:
    return (ROOT / "SECURITY.md").read_text(encoding="utf-8")


def test_security_table_matches_the_generated_one() -> None:
    generator = load_generator()
    security = read_security()
    expected = generator.replace_region(
        security, generator.BEGIN, generator.END, generator.render_table(), "SECURITY.md"
    )
    assert security == expected


def test_a_hand_edited_table_no_longer_matches() -> None:
    generator = load_generator()
    security = read_security()
    edited = security.replace("| 標準入力 |", "| 標準入力! |", 1)
    assert edited != security
    expected = generator.replace_region(
        edited, generator.BEGIN, generator.END, generator.render_table(), "SECURITY.md"
    )
    assert edited != expected


def test_the_removed_input_is_listed_apart_and_every_other_input_has_a_row() -> None:
    generator = load_generator()
    passed, _, not_passed = generator.render_table().partition("#### 渡さないもの")
    for item in ClaudeInput:
        content = generator.cell(item.value.content)
        if item is ClaudeInput.REMOVED_ENVIRONMENT:
            assert content in not_passed
            assert content not in passed
        else:
            assert content in passed


def test_every_stripped_environment_variable_appears_in_the_not_passed_table() -> None:
    generator = load_generator()
    _, _, not_passed = generator.render_table().partition("#### 渡さないもの")
    assert REVIEWER_STRIPPED_ENVIRONMENT_VARIABLES
    for name in REVIEWER_STRIPPED_ENVIRONMENT_VARIABLES:
        assert name in not_passed


def test_the_table_has_no_template_placeholder_names() -> None:
    generator = load_generator()
    assert re.search(r"\$\w", generator.render_table()) is None


def test_every_channel_has_a_display_name() -> None:
    assert set(load_generator().CHANNEL_NAMES) == set(Channel)


def test_the_table_lists_groups_only_in_group_order() -> None:
    generator = load_generator()
    table = generator.render_table()
    assert "alternative" not in table
    assert "defense" not in table
    assert "micro、security" in table


def test_a_pipe_and_a_newline_in_a_cell_do_not_break_the_row() -> None:
    generator = load_generator()
    assert generator.cell("a|b\nc") == "a\\|b c"


def test_the_sentences_before_the_table_are_inside_the_generated_region() -> None:
    generator = load_generator()
    table = generator.render_table()
    assert generator.OUTSIDE_SENTENCE in table
    assert generator.GROUPS_SENTENCE in table
    assert generator.ENVIRONMENT_SENTENCE in table
    assert generator.OUTSIDE_SENTENCE in read_security()


def test_the_table_shows_neither_role_names_nor_internal_shard_wording() -> None:
    generator = load_generator()
    table = generator.render_table()
    group_names = {group.value for group in GroupId}
    for role in Role:
        if role.value not in group_names:
            assert role.value not in table
    assert "シャード" not in table
    assert "argv" not in table


def test_security_without_the_markers_is_rejected() -> None:
    generator = load_generator()
    with pytest.raises(ValueError, match="印"):
        generator.replace_region(
            "# 印の無い文書\n", generator.BEGIN, generator.END, "表", "SECURITY.md"
        )
