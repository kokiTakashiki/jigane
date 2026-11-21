from pathlib import Path
from types import ModuleType

import pytest
from script_loader import import_script

ROOT = Path(__file__).parent.parent


def load_generator() -> ModuleType:
    return import_script("generate_command_table")


def test_commands_document_table_matches_the_generated_one() -> None:
    generator = load_generator()
    document = (ROOT / "docs" / "commands.md").read_text(encoding="utf-8")
    expected = generator.replace_region(
        document, generator.render_table(generator.subcommand_helps())
    )
    assert document == expected


def test_a_hand_edited_table_no_longer_matches() -> None:
    generator = load_generator()
    document = (ROOT / "docs" / "commands.md").read_text(encoding="utf-8")
    edited = document.replace("| `new` |", "| `new!` |", 1)
    assert edited != document
    expected = generator.replace_region(
        edited, generator.render_table(generator.subcommand_helps())
    )
    assert edited != expected


def test_main_regenerates_the_commands_document_without_writing_to_readme(tmp_path: Path) -> None:
    generator = load_generator()
    old_table = f"{generator.BEGIN}\n\n古い表\n\n{generator.END}\n"
    document = tmp_path / "docs" / "commands.md"
    document.parent.mkdir()
    document.write_text(f"## コマンド一覧\n\n{old_table}", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(old_table, encoding="utf-8")

    assert generator.main(["--root", str(tmp_path)]) == 0

    table = generator.render_table(generator.subcommand_helps())
    assert document.read_text(encoding="utf-8") == f"## コマンド一覧\n\n{table}\n"
    assert readme.read_text(encoding="utf-8") == old_table


def test_every_subcommand_is_in_exactly_one_stage() -> None:
    generator = load_generator()
    assigned = [name for _, names in generator.STAGES for name in names]
    assert sorted(assigned) == sorted(generator.subcommand_helps())


def test_a_subcommand_missing_from_the_stages_is_rejected() -> None:
    generator = load_generator()
    helps = {**generator.subcommand_helps(), "brand-new": "足したばかり"}
    with pytest.raises(ValueError, match="brand-new"):
        generator.render_table(helps)


def test_a_stage_naming_a_missing_subcommand_is_rejected() -> None:
    generator = load_generator()
    helps = generator.subcommand_helps()
    del helps["new"]
    with pytest.raises(ValueError, match="new"):
        generator.render_table(helps)


def test_a_subcommand_in_two_stages_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    generator = load_generator()
    monkeypatch.setattr(generator, "STAGES", (*generator.STAGES, ("重複", ("new",))))
    with pytest.raises(ValueError, match="new"):
        generator.render_table(generator.subcommand_helps())


def test_commands_document_without_the_markers_is_rejected() -> None:
    generator = load_generator()
    with pytest.raises(ValueError, match="印"):
        generator.replace_region("# コマンドの使い方\n", "表")


def test_only_run_and_debug_launch_models() -> None:
    generator = load_generator()
    launching = {name for name in generator.subcommand_helps() if generator.launches_model(name)}
    assert launching == {"run", "debug"}


def test_only_interactive_launches_ask_for_terminal_consent() -> None:
    generator = load_generator()
    launching = {name for name in generator.subcommand_helps() if generator.launches_model(name)}
    # 利用規約の同意（terms_consent）は別の同意なので、require_consent だけを数える
    consenting = {
        name
        for name in generator.subcommand_helps()
        if generator.calls_function(name, "require_consent")
    }
    assert consenting == launching - {"debug"}
