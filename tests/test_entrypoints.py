import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from markdown_support import fences

from jigane.git.environment import GIT_LOCATION_VARIABLES

ROOT = Path(__file__).parent.parent


def load_generator() -> ModuleType:
    path = ROOT / "scripts" / "generate_entrypoints.py"
    spec = importlib.util.spec_from_file_location("generate_entrypoints", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_makefile_matches_the_definition() -> None:
    generator = load_generator()
    expected = generator.render_makefile(generator.load())
    assert (ROOT / "Makefile").read_text(encoding="utf-8") == expected


def test_justfile_matches_the_definition() -> None:
    generator = load_generator()
    expected = generator.render_justfile(generator.load())
    assert (ROOT / "justfile").read_text(encoding="utf-8") == expected


# just を起動する試験は置かない。macOS と Linux の入口は make で、
# ゲートを just の無い環境でも回すため。
# justfile は、生成物が定義と一字一句一致することと、
# set quiet があり本文の行頭が @ でないことだけを確かめる
# make を起動する試験は POSIX だけの置き場 tests_posix/test_make_entrypoints.py にある


def test_readme_usage_code_blocks_start_with_jigane() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    blocks = fences(readme, "## 使い方")
    assert blocks
    for block in blocks:
        assert block.info == "bash"
        assert block.content.startswith("jigane "), block.content


def entrypoint_target(command: str) -> str:
    """`make -f X jigane ARGS=...` や `just --justfile X jigane` から、作業の名前を取り出す。"""
    words = command.split()
    options_with_value = {"-f", "--justfile"}
    index = 1
    while words[index].startswith("-"):
        index += 2 if words[index] in options_with_value else 1
    return words[index]


def test_contributing_entrypoint_examples_name_defined_tasks() -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    blocks = fences(contributing, "## 開発の環境")
    tasks = {task.name for task in load_generator().load().tasks}
    seen: set[str] = set()
    for block in blocks:
        body = block.content
        assert block.info == "bash"
        tool = body.split(maxsplit=1)[0]
        assert tool in {"make", "just"}, body
        assert entrypoint_target(body) in tasks, body
        seen.add(tool)
    assert seen == {"make", "just"}


FACTS_TOML = """\
[entrypoints]
summary = "s"
regenerate_task = "a"
uv_missing_message = "m"
uv_install_message = "i"
uv_install_url = "u"
clone_url = "https://example.test/o/r"
"""
TASK_HEAD = '[[task]]\nname = "a"\ndescription = "d"\n'


def load_text(tmp_path: Path, body: str) -> Any:  # noqa: ANN401 動的に読んだ生成スクリプトの型は静的に書けない
    path = tmp_path / "entrypoints.toml"
    path.write_text(body, encoding="utf-8")
    return load_generator().load(path)


def test_load_rejects_an_unknown_regenerate_task(tmp_path: Path) -> None:
    body = FACTS_TOML.replace('regenerate_task = "a"', 'regenerate_task = "b"')
    with pytest.raises(ValueError, match="regenerate_task"):
        load_text(tmp_path, body + TASK_HEAD + 'commands = ["echo"]\n')


def test_load_accepts_a_minimal_definition(tmp_path: Path) -> None:
    definition = load_text(tmp_path, FACTS_TOML + TASK_HEAD + 'commands = ["echo {args}"]\n')
    assert [task.name for task in definition.tasks] == ["a"]
    assert definition.tasks[0].uses_args


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (FACTS_TOML.replace('uv_install_url = "u"\n', ""), "不足"),
        (FACTS_TOML + 'extra = "x"\n', "未知"),
        ("[unknown]\nkey = 1\n" + FACTS_TOML, "未知の節"),
        (TASK_HEAD + 'commands = ["x"]\n', r"\[entrypoints\] が無い"),
        (FACTS_TOML + TASK_HEAD, "どちらかは持つ"),
        (FACTS_TOML + TASK_HEAD + 'commands = ["x"]\nextra = 1\n', "未知の鍵"),
        (FACTS_TOML + TASK_HEAD + 'depends = ["nowhere"]\n', "未定義の作業"),
        (FACTS_TOML + TASK_HEAD + 'commands = ["{nope}"]\n', "未定義の置換"),
        (FACTS_TOML + TASK_HEAD + 'commands = ["x"]\n' + TASK_HEAD + 'commands = ["y"]\n', "重複"),
    ],
)
def test_load_rejects_an_invalid_definition(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_text(tmp_path, body)


def test_subproject_placeholders_point_under_the_project(tmp_path: Path) -> None:
    generator = load_generator()
    body = (
        FACTS_TOML
        + 'subproject = "cli"\n'
        + TASK_HEAD
        + 'commands = ["{subproject_task} check", "uv run --project {subproject_dir} x"]\n'
    )
    definition = load_text(tmp_path, body)
    makefile = generator.render_makefile(definition)
    justfile = generator.render_justfile(definition)
    assert '$(MAKE) --no-print-directory -C "$(PROJECT_DIR)cli" check' in makefile
    assert 'uv run --project "$(PROJECT_DIR)cli" x' in makefile
    assert 'just --justfile {{quote(justfile_directory() / "cli" / "justfile")}} check' in justfile
    assert 'uv run --project {{quote(justfile_directory() / "cli")}} x' in justfile


def test_self_task_names_the_tool_of_each_dialect(tmp_path: Path) -> None:
    generator = load_generator()
    body = FACTS_TOML + TASK_HEAD + 'commands = ["run {self_task} check"]\n'
    definition = load_text(tmp_path, body)
    assert "\t@run make check\n" in generator.render_makefile(definition)
    assert "    run just check\n" in generator.render_justfile(definition)


def test_just_joins_a_path_after_a_tree_placeholder_inside_one_quote(tmp_path: Path) -> None:
    generator = load_generator()
    body = (
        FACTS_TOML
        + 'subproject = "cli"\n'
        + TASK_HEAD
        + 'commands = ["py {project_dir}/scripts/a.py x", "py {project_dir}web y",'
        + ' "py {subproject_dir}/scripts/b.py z", "py {project_dir} w"]\n'
    )
    definition = load_text(tmp_path, body)
    makefile = generator.render_makefile(definition)
    justfile = generator.render_justfile(definition)
    assert 'py {{quote(justfile_directory() / "scripts/a.py")}} x' in justfile
    assert 'py {{quote(justfile_directory() / "web")}} y' in justfile
    assert 'py {{quote(justfile_directory() / "cli" / "scripts/b.py")}} z' in justfile
    assert "py {{quote(justfile_directory())}} w" in justfile
    assert "}}/" not in justfile
    assert 'py "$(PROJECT_DIR)"/scripts/a.py x' in makefile


@pytest.mark.parametrize(
    ("facts", "message"),
    [
        (FACTS_TOML, "subproject の置換"),
        (FACTS_TOML + 'subproject = "../up"\n', "一つの名前にする"),
    ],
)
def test_subproject_placeholders_need_a_plain_subproject(
    tmp_path: Path, facts: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_text(tmp_path, facts + TASK_HEAD + 'commands = ["{subproject_task} check"]\n')


def test_require_git_worktree_adds_the_check_to_both_dialects(tmp_path: Path) -> None:
    generator = load_generator()
    body = FACTS_TOML + TASK_HEAD + 'require_git_worktree = true\ncommands = ["echo"]\n'
    definition = load_text(tmp_path, body)
    makefile = generator.render_makefile(definition)
    justfile = generator.render_justfile(definition)
    assert "\t$(call require_git_worktree)\n\t@echo\n" in makefile
    assert "a: _require-git-worktree\n" in justfile
    assert "[unix]\n_require-git-worktree:" in justfile
    assert "[windows]\n_require-git-worktree:" in justfile


def test_without_require_git_worktree_the_check_is_not_called(tmp_path: Path) -> None:
    generator = load_generator()
    definition = load_text(tmp_path, FACTS_TOML + TASK_HEAD + 'commands = ["echo"]\n')
    assert "\t$(call require_git_worktree)" not in generator.render_makefile(definition)
    assert "_require-git-worktree" not in generator.render_justfile(definition)


@pytest.mark.parametrize(
    ("task_body", "message"),
    [
        ('require_git_worktree = "yes"\ncommands = ["x"]\n', "真偽値"),
        ('require_git_worktree = true\ndepends = ["a"]\n', "commands を持つ作業にだけ"),
    ],
)
def test_load_rejects_an_invalid_require_git_worktree(
    tmp_path: Path, task_body: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_text(tmp_path, FACTS_TOML + TASK_HEAD + task_body)


@pytest.mark.parametrize(
    ("clone_url", "recurse", "zip_note"),
    [
        ("https://github.com/kokiTakashiki/jigane", False, False),
        ("https://example.test/org/jigane", True, True),
    ],
)
def test_git_worktree_guidance_is_pasteable_in_every_dialect(
    tmp_path: Path, clone_url: str, *, recurse: bool, zip_note: bool
) -> None:
    generator = load_generator()
    facts = FACTS_TOML.replace("https://example.test/o/r", clone_url)
    facts += 'subproject = "cli"\n' + f"recurse_submodules = {str(recurse).lower()}\n"
    body = facts + TASK_HEAD + 'require_git_worktree = true\ncommands = ["echo"]\n'
    definition = load_text(tmp_path, body)
    clone = ("git clone --recurse-submodules " if recurse else "git clone ") + clone_url
    note = "GitHub の ZIP では cli が入らないため、動きません。"
    makefile = generator.render_makefile(definition)
    justfile = generator.render_justfile(definition)
    for line in (clone, "cd jigane", "make setup"):
        assert f'echo "    {line}" >&2' in makefile
    for line in (clone, "cd jigane", "just setup"):
        assert f"echo '    {line}' >&2" in justfile
        assert f"[Console]::Error.WriteLine('    {line}')" in justfile
    for text in (makefile, justfile):
        assert (note in text) is zip_note
        assert ("--recurse-submodules" in text) is recurse


def test_load_rejects_require_git_worktree_without_clone_url(tmp_path: Path) -> None:
    facts = FACTS_TOML.replace('clone_url = "https://example.test/o/r"\n', "")
    body = facts + TASK_HEAD + 'require_git_worktree = true\ncommands = ["echo"]\n'
    with pytest.raises(ValueError, match="clone_url"):
        load_text(tmp_path, body)


@pytest.mark.parametrize(
    ("facts", "message"),
    [
        (
            FACTS_TOML.replace('clone_url = "https://example.test/o/r"\n', "")
            + "recurse_submodules = true\n",
            "clone_url",
        ),
        (FACTS_TOML + "recurse_submodules = true\n", "subproject"),
    ],
)
def test_load_rejects_a_half_specified_clone_guide(
    tmp_path: Path, facts: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_text(tmp_path, facts + TASK_HEAD + 'commands = ["echo"]\n')


def test_load_builds_no_clone_guide_without_clone_url(tmp_path: Path) -> None:
    facts = FACTS_TOML.replace('clone_url = "https://example.test/o/r"\n', "")
    definition = load_text(tmp_path, facts + TASK_HEAD + 'commands = ["echo"]\n')
    assert definition.facts.clone_guide is None


def test_load_builds_a_clone_guide_from_url_and_submodule(tmp_path: Path) -> None:
    facts = FACTS_TOML + 'subproject = "cli"\nrecurse_submodules = true\n'
    guide = load_text(tmp_path, facts + TASK_HEAD + 'commands = ["echo"]\n').facts.clone_guide
    assert (guide.url, guide.submodule) == ("https://example.test/o/r", "cli")


GUARD_FACTS_TOML = (
    FACTS_TOML
    + 'subproject = "sub"\nsubproject_executable = "tool"\nlegacy_path = "old"\n'
    + '[[tool]]\nname = "just"\nknown_broken = "1.40.0"\nverified = "1.58.0"\nupdate = "U1"\n'
    + '[[tool]]\nname = "git"\nupdate = "U2"\n'
)
GUARD_TASK = (
    TASK_HEAD
    + "require_tools = true\nwarn_legacy_path = true\nrequire_subproject_env = true\n"
    + 'commands = ["echo"]\n'
)


def test_guard_flags_add_every_check_to_both_dialects(tmp_path: Path) -> None:
    generator = load_generator()
    definition = load_text(tmp_path, GUARD_FACTS_TOML + GUARD_TASK)
    makefile = generator.render_makefile(definition)
    justfile = generator.render_justfile(definition)
    assert (
        "\t$(call require_tools)\n\t$(call warn_legacy_path)\n"
        "\t$(call require_subproject_env)\n\t@echo\n"
    ) in makefile
    assert "a: _require-tools _warn-legacy-path _require-subproject-env\n" in justfile
    for name in ("_require-tools", "_warn-legacy-path", "_require-subproject-env"):
        assert f"[unix]\n{name}:" in justfile
        assert f"[windows]\n{name}:" in justfile


def test_guards_without_a_flag_are_not_emitted(tmp_path: Path) -> None:
    generator = load_generator()
    definition = load_text(tmp_path, GUARD_FACTS_TOML + TASK_HEAD + 'commands = ["echo"]\n')
    makefile = generator.render_makefile(definition)
    justfile = generator.render_justfile(definition)
    for name in ("require_tools", "warn_legacy_path", "require_subproject_env"):
        assert name not in makefile
    for name in ("_require-tools", "_warn-legacy-path", "_require-subproject-env"):
        assert name not in justfile


def test_tool_guidance_compares_versions_in_every_dialect(tmp_path: Path) -> None:
    generator = load_generator()
    definition = load_text(tmp_path, GUARD_FACTS_TOML + GUARD_TASK)
    makefile = generator.render_makefile(definition)
    justfile = generator.render_justfile(definition)
    missing = "  x just が見つかりません。U1"
    old = "  x just が古いです（${v}）。1.58.0 以上に更新してください: U1"
    assert f'echo "{missing}" >&2' in justfile
    assert f'echo "{old}" >&2' in justfile
    assert f'echo "{old}" >&2'.replace("$", "$$") in makefile
    unverified = "! just ${v} は確かめていない版です。動かないときは 1.58.0 以上に更新してください"
    assert f'echo "{unverified}" >&2' in justfile
    assert f'echo "{unverified}" >&2'.replace("$", "$$") in makefile
    assert "[version]'1.40.0'" in justfile
    assert "[version]'1.58.0'" in justfile
    assert (
        "[Console]::Error.WriteLine('! just ' + $m.Value + ' は確かめていない版です。" in justfile
    )
    assert "[Console]::Error.WriteLine('  x just が古いです（' + $m.Value + '）。" in justfile
    # 最低の版を持たない道具は、有無だけを確かめる
    assert "x git が古いです" not in makefile
    assert 'echo "  x git が見つかりません。U2" >&2' in makefile


def test_environment_and_leftover_guidance_in_every_dialect(tmp_path: Path) -> None:
    generator = load_generator()
    definition = load_text(tmp_path, GUARD_FACTS_TOML + GUARD_TASK)
    makefile = generator.render_makefile(definition)
    justfile = generator.render_justfile(definition)
    assert "  x cli の環境がありません。先に make setup を走らせてください。" in makefile
    assert '[ -x "$(PROJECT_DIR)sub/.venv/bin/tool" ]' in makefile
    assert "  x cli の環境がありません。先に just setup を走らせてください。" in justfile
    assert '"sub" / ".venv/bin/tool"' in justfile
    assert '"sub" / ".venv/Scripts/tool.exe"' in justfile
    leftover = "  ! 根に旧い old/ の残り（追跡外のファイル）があります。消してから使ってください: "
    assert f"{leftover}rm -r old" in makefile
    assert f"{leftover}rm -r old" in justfile
    assert f"{leftover}Remove-Item -Recurse -Force old" in justfile


@pytest.mark.parametrize(
    ("facts", "task_body", "message"),
    [
        (FACTS_TOML, "require_tools = true\n", r"require_tools は \[\[tool\]\]"),
        (FACTS_TOML, "warn_legacy_path = true\n", "warn_legacy_path は legacy_path"),
        (FACTS_TOML, "require_subproject_env = true\n", "require_subproject_env は subproject"),
        (GUARD_FACTS_TOML, 'require_tools = "yes"\n', "真偽値"),
        (GUARD_FACTS_TOML, 'depends = ["a"]\nrequire_tools = true\n', "commands を持つ作業にだけ"),
    ],
)
def test_load_rejects_an_invalid_guard_flag(
    tmp_path: Path, facts: str, task_body: str, message: str
) -> None:
    body = facts + TASK_HEAD + task_body
    if "depends" not in task_body:
        body += 'commands = ["echo"]\n'
    with pytest.raises(ValueError, match=message):
        load_text(tmp_path, body)


@pytest.mark.parametrize(
    ("versions", "message"),
    [
        ('known_broken = "1.x"\nverified = "1.58.0"\n', r"数\.数\.数"),
        ('known_broken = "1.40.0"\nverified = "1.58"\n', r"数\.数\.数"),
        ('verified = "1.58.0"\n', "両方"),
        ('known_broken = "1.40.0"\n', "両方"),
        ('known_broken = "1.58.0"\nverified = "1.58.0"\n', "verified より古く"),
    ],
)
def test_load_rejects_malformed_tool_versions(tmp_path: Path, versions: str, message: str) -> None:
    body = FACTS_TOML + '[[tool]]\nname = "x"\n' + versions + 'update = "u"\n'
    with pytest.raises(ValueError, match=message):
        load_text(tmp_path, body + TASK_HEAD + 'commands = ["echo"]\n')


def test_every_make_define_is_called_by_some_task(tmp_path: Path) -> None:
    generator = load_generator()
    for definition in (
        generator.load(),
        load_text(tmp_path, FACTS_TOML + TASK_HEAD + 'commands = ["echo"]\n'),
        load_text(tmp_path, GUARD_FACTS_TOML + GUARD_TASK),
    ):
        makefile = generator.render_makefile(definition)
        defined = re.findall(r"^define (\w+)$", makefile, re.MULTILINE)
        assert defined
        for name in defined:
            assert f"$(call {name})" in makefile, name


def test_just_recipes_are_quiet_without_at_prefix(tmp_path: Path) -> None:
    generator = load_generator()
    body = GUARD_FACTS_TOML + GUARD_TASK + "require_git_worktree = true\n"
    justfile = generator.render_justfile(load_text(tmp_path, body))
    assert re.search(r"^set quiet$", justfile, re.MULTILINE)
    # set quiet の下の行頭の @ は表示への切り替えになる
    assert re.findall(r"^ +@", justfile, re.MULTILINE) == []


def test_show_commands_prints_the_command_in_both_dialects(tmp_path: Path) -> None:
    generator = load_generator()
    body = FACTS_TOML + TASK_HEAD + 'show_commands = true\ncommands = ["echo one"]\n'
    definition = load_text(tmp_path, body)
    assert "\techo one\n" in generator.render_makefile(definition)
    assert "\t@echo one\n" not in generator.render_makefile(definition)
    justfile = generator.render_justfile(definition)
    assert "\n[no-quiet]\na:\n    echo one\n" in justfile
    assert re.findall(r"^ +@", justfile, re.MULTILINE) == []


def test_commands_are_hidden_unless_show_commands_is_set(tmp_path: Path) -> None:
    generator = load_generator()
    definition = load_text(tmp_path, FACTS_TOML + TASK_HEAD + 'commands = ["echo one"]\n')
    assert "\t@echo one\n" in generator.render_makefile(definition)
    justfile = generator.render_justfile(definition)
    assert "\na:\n    echo one\n" in justfile
    assert "\n[no-quiet]\n" not in justfile


def test_posix_commands_run_only_from_the_makefile(tmp_path: Path) -> None:
    generator = load_generator()
    body = FACTS_TOML + TASK_HEAD + 'commands = ["echo one"]\nposix_commands = ["echo posix"]\n'
    definition = load_text(tmp_path, body)
    assert "\t@echo one\n\t@echo posix\n" in generator.render_makefile(definition)
    justfile = generator.render_justfile(definition)
    assert "echo one" in justfile
    assert "posix" not in justfile


def test_a_task_runs_its_commands_after_its_dependencies(tmp_path: Path) -> None:
    generator = load_generator()
    body = FACTS_TOML + TASK_HEAD + 'commands = ["echo one"]\n'
    body += '[[task]]\nname = "b"\ndescription = "d"\ndepends = ["a"]\ncommands = ["echo two"]\n'
    definition = load_text(tmp_path, body)
    assert "b: a\n\t$(call require_uv)\n\t@echo two\n" in generator.render_makefile(definition)
    assert "b: a\n    echo two\n" in generator.render_justfile(definition)


def test_load_rejects_posix_commands_without_commands(tmp_path: Path) -> None:
    body = FACTS_TOML + TASK_HEAD + 'depends = ["a"]\nposix_commands = ["echo posix"]\n'
    with pytest.raises(ValueError, match="posix_commands"):
        load_text(tmp_path, body)


def test_posix_commands_reject_unknown_placeholders(tmp_path: Path) -> None:
    body = FACTS_TOML + TASK_HEAD + 'commands = ["echo"]\nposix_commands = ["echo {nope}"]\n'
    with pytest.raises(ValueError, match="未定義の置換"):
        load_text(tmp_path, body)


def test_show_commands_keeps_the_uv_check_hidden(tmp_path: Path) -> None:
    generator = load_generator()
    body = FACTS_TOML + TASK_HEAD + 'show_commands = true\ncommands = ["echo one"]\n'
    makefile = generator.render_makefile(load_text(tmp_path, body))
    assert "a:\n\t$(call require_uv)\n\techo one\n" in makefile
    assert "@command -v uv" in makefile


@pytest.mark.parametrize(
    ("task_body", "message"),
    [
        ('show_commands = "yes"\ncommands = ["echo"]\n', "show_commands"),
        ('show_commands = true\ndepends = ["a"]\n', "show_commands"),
    ],
)
def test_load_rejects_an_invalid_show_commands(
    tmp_path: Path, task_body: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_text(tmp_path, FACTS_TOML + TASK_HEAD + task_body)


def test_the_gate_tasks_show_their_commands() -> None:
    tasks = {task.name: task for task in load_generator().load().tasks}
    shown = {name for name, task in tasks.items() if task.show_commands}
    assert shown == {"setup", "lint", "fmt", "test", "changed-tests"}


def test_generated_git_lines_drop_the_inherited_location_variables(tmp_path: Path) -> None:
    generator = load_generator()
    body = GUARD_FACTS_TOML + GUARD_TASK + "require_git_worktree = true\n"
    definition = load_text(tmp_path, body)
    lines = (
        generator.render_makefile(definition) + generator.render_justfile(definition)
    ).splitlines()
    git_lines = [line for line in lines if "git -C" in line]
    assert git_lines
    for line in git_lines:
        # unix 側は env -u、windows 側は Remove-Item Env: で、向き先の変数を外している
        unix = "env " + " ".join(f"-u {name}" for name in GIT_LOCATION_VARIABLES) + " git"
        windows = [f"Remove-Item Env:{name} " for name in GIT_LOCATION_VARIABLES]
        assert unix in line or all(part in line for part in windows), line


def test_generated_entrypoints_unexport_the_outer_virtual_env() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    justfile = (ROOT / "justfile").read_text(encoding="utf-8")
    assert "\nunexport VIRTUAL_ENV\n" in makefile
    assert "\nunexport VIRTUAL_ENV\n" in justfile


def test_generated_entrypoints_unexport_pythonpath() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    justfile = (ROOT / "justfile").read_text(encoding="utf-8")
    assert "\nunexport PYTHONPATH\n" in makefile
    assert "\nunexport PYTHONPATH\n" in justfile
