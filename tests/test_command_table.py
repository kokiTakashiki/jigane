import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent


def load_generator() -> ModuleType:
    path = ROOT / "scripts" / "generate_command_table.py"
    spec = importlib.util.spec_from_file_location("generate_command_table", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_readme_table_matches_the_generated_one() -> None:
    generator = load_generator()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    expected = generator.replace_region(
        readme, generator.render_table(generator.subcommand_helps())
    )
    assert readme == expected


def test_a_hand_edited_table_no_longer_matches() -> None:
    generator = load_generator()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    edited = readme.replace("| `new` |", "| `new!` |", 1)
    assert edited != readme
    expected = generator.replace_region(
        edited, generator.render_table(generator.subcommand_helps())
    )
    assert edited != expected


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


def test_readme_without_the_markers_is_rejected() -> None:
    generator = load_generator()
    with pytest.raises(ValueError, match="印"):
        generator.replace_region("# 印の無い README\n", "表")


def test_only_run_launches_claude() -> None:
    generator = load_generator()
    launching = {name for name in generator.subcommand_helps() if generator.launches_claude(name)}
    assert launching == {"run"}


def test_launching_subcommands_are_the_ones_asking_for_launch_consent() -> None:
    generator = load_generator()
    launching = {name for name in generator.subcommand_helps() if generator.launches_claude(name)}
    # 利用規約の同意（terms_consent）は別の同意なので、require_consent だけを数える
    consenting = {
        name
        for name in generator.subcommand_helps()
        if generator.calls_function(name, "require_consent")
    }
    assert launching == consenting
