from pathlib import Path

import pytest

from jigane.findings.discard import ShardConcerns
from jigane.groups.definitions import GroupId
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.skills import adversarial, config_head, macro, sharded
from jigane.skills.assembly import AssemblyInput
from jigane.skills.shards import DiffShard, FullTextShard, ShardPlan, ShardPlanKind, Unit

JUSTFILE = "set unstable\nset lists\n\nbuild:\n    echo hi\n"
MAKEFILE = "SHELL := /bin/bash\n.PHONY: all\n\nall:\n\techo hi\n"


@pytest.fixture
def review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "justfile").write_text(JUSTFILE, encoding="utf-8")
    (root / "src" / "a.py").write_text("set lists = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return create_feature_review(root, ["justfile", "src"], "p")


def unit(path: str) -> Unit:
    return Unit(path, f"=== {path} ===\n@@ diff\n", 10)


def test_justfile_head_keeps_set_lines_only() -> None:
    assert config_head.config_head("justfile", JUSTFILE) == "set unstable\nset lists"


def test_justfile_head_keeps_optional_import_and_mod() -> None:
    text = 'set unstable\nimport? "x.just"\nmod? name\nimport "y.just"\n\nbuild:\n    echo\n'
    head = config_head.config_head("justfile", text)
    assert head == 'set unstable\nimport? "x.just"\nmod? name\nimport "y.just"'


def test_makefile_head_stops_before_the_first_rule() -> None:
    head = config_head.config_head("sub/Makefile", MAKEFILE)
    assert head == "SHELL := /bin/bash\n.PHONY: all\n"


def test_pyproject_head_keeps_tool_tables() -> None:
    text = '[project]\nname = "x"\n\n[tool.ruff]\nline-length = 99\n'
    assert config_head.config_head("pyproject.toml", text) == "[tool.ruff]\nline-length = 99"


def test_head_falls_back_to_the_first_lines_when_nothing_is_extracted() -> None:
    assert config_head.config_head("justfile", "build:\n    echo hi\n") == "build:\n    echo hi"


def test_non_config_file_has_no_head() -> None:
    assert config_head.config_head("src/a.py", "set lists = 1\n") is None


def test_diff_shard_with_justfile_gets_the_set_lines(review: Review) -> None:
    prompt = sharded.build_shard_prompt(
        GroupId.MICRO,
        AssemblyInput.load(review),
        DiffShard((unit("justfile"),)),
        1,
        1,
        concerns=ShardConcerns(),
    )
    assert "=== justfile （冒頭の設定） ===\nset unstable\nset lists" in prompt


def test_diff_shard_with_python_gets_no_head(review: Review) -> None:
    prompt = sharded.build_shard_prompt(
        GroupId.MICRO,
        AssemblyInput.load(review),
        DiffShard((unit("src/a.py"),)),
        1,
        1,
        concerns=ShardConcerns(),
    )
    assert "（冒頭の設定） ===" not in prompt


def test_full_text_shard_gets_no_extra_head(review: Review) -> None:
    prompt = sharded.build_shard_prompt(
        GroupId.MICRO,
        AssemblyInput.load(review),
        FullTextShard((unit("justfile"),)),
        1,
        1,
        concerns=ShardConcerns(),
    )
    assert "=== justfile （冒頭の設定） ===" not in prompt


def test_rebuttal_changes_carry_the_head(review: Review, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macro, "verifier_changes_text", lambda _review: "### 変更\n\ndiff")
    plan = ShardPlan(ShardPlanKind.DIFF, (unit("justfile"), unit("src/a.py")), ())
    changes = sharded._changes_with_config_heads(review, plan)
    assert changes.startswith("### 変更\n\ndiff")
    assert "=== justfile （冒頭の設定） ===\nset unstable\nset lists" in changes
    only_python = ShardPlan(ShardPlanKind.DIFF, (unit("src/a.py"),), ())
    assert sharded._changes_with_config_heads(review, only_python) == "### 変更\n\ndiff"
    rendered = adversarial.render_prompt("rebuttal", changes, candidates_json=[])
    assert "set unstable" in rendered.text


def test_plan_other_than_diff_adds_nothing(
    review: Review, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(macro, "changes_prompt_text", lambda _review: "### 変更")
    plan = ShardPlan(ShardPlanKind.FILES, (unit("justfile"),), ())
    assert sharded._changes_with_config_heads(review, plan) == "### 変更"


@pytest.mark.parametrize(
    ("name", "sentence"),
    [
        ("rebuttal", "設定ファイル（justfile、Makefile、pyproject.toml など）の指摘では"),
        ("micro", "設定ファイルの挙動を述べるときは"),
        ("security", "設定ファイルの挙動を述べるときは"),
    ],
)
def test_prompt_carries_the_sentence_once(name: str, sentence: str) -> None:
    assert adversarial.template_text(name).count(sentence) == 1
