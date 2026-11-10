"""設計書 complexity.md 7 章の固定試験。値の組の表、共変更の表、資料の長さを合成の履歴で確かめる。

jigane 自身の履歴には依存しない。観点の識別子の組を二つのファイルが写し、別のファイルが
組の一要素を文字列で比べる形と、構造の依存が無いのに一緒に変わり続けるファイルの対を、
小さなリポジトリに作る。
"""

import json
from dataclasses import replace
from pathlib import Path

from pr_support import commit_all, git
from review_fakes import make_review
from test_policy_classes import whole
from test_policy_input_limit import diagram_defaults, stops

from jigane.policy_materials import cochange
from jigane.reviews.lifecycle import LocalRange, RangeOrigin, Review
from jigane.reviews.review_formats import PAIRS_FILE
from jigane.skills import policy_cochange, policy_value_sets
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_principles import principle_prompts
from jigane.storage.store import ReviewKind

VIEWPOINTS = ("M1", "M2", "M3", "M4", "M5")
COMMON_FILES = 100
# security.py は micro.py の変更 6 回すべてに付き添う。強さは 6 / ((20 + 6) / 2)
STRONG_COMMITS = 6
# pipeline.py と micro.py は 5 回一緒に変わるが、それぞれの変更が 20 回で、強さは 5 / 20
WEAK_SHARED_COMMITS = 5
MICRO_SOLO_COMMITS = 9
PIPELINE_SOLO_COMMITS = 15


def _write(root: Path, name: str, number: int) -> None:
    texts = {
        "micro.py": f"MICRO_VIEWPOINTS = {VIEWPOINTS!r}\n# {number}\n",
        "pipeline.py": f'def run(v):\n    return v == "M5"  # {number}\n',
        "security.py": f"y = {number}\n",
    }
    (root / name).write_text(texts[name], encoding="utf-8")


def _synthetic_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    # 値が珍しくなるための共通のファイル。多くのファイルを変えたコミットなので共変更には数えない
    for number in range(COMMON_FILES):
        (root / f"common{number}.py").write_text("x = 1\n", encoding="utf-8")
    (root / "macro.py").write_text(f"MACRO_VIEWPOINTS = {VIEWPOINTS!r}\n", encoding="utf-8")
    for name in ("micro.py", "pipeline.py", "security.py"):
        _write(root, name, 0)
    commit_all(root, "base")
    plan = (
        [("micro.py", "security.py")] * STRONG_COMMITS
        + [("micro.py", "pipeline.py")] * WEAK_SHARED_COMMITS
        + [("micro.py",)] * MICRO_SOLO_COMMITS
        + [("pipeline.py",)] * PIPELINE_SOLO_COMMITS
    )
    for number, names in enumerate(plan, start=1):
        for name in names:
            _write(root, name, number)
        commit_all(root, f"change {number}")
    return root


def _review(tmp_path: Path, repo: Path) -> Review:
    base = tmp_path / "stored-base"
    base.mkdir()
    changed = ["macro.py", "micro.py", "pipeline.py"]
    review = make_review(
        repo,
        changed,
        base_tree=base,
        judgment_map={
            "segments": [whole(path, 2) for path in changed],
            "regions": [],
            "line_counts": {},
        },
        kind=ReviewKind.RANGE,
    )
    origin = review.origin
    assert isinstance(origin, RangeOrigin)
    head = git(repo, "rev-parse", "HEAD").strip()
    review.origin = RangeOrigin(
        LocalRange(origin.range.range_text, str(repo)), replace(origin.diff, head_sha=head)
    )
    # 値の組の表は保存した差分から足した行を読む。変更したファイルを全行足した差分にする
    directory = review.id.directory()
    directory.mkdir(parents=True)
    (directory / PAIRS_FILE).write_text(
        json.dumps([[path, path] for path in changed]), encoding="utf-8"
    )
    diff = ""
    for path in changed:
        lines = (repo / path).read_text(encoding="utf-8").splitlines()
        added = "".join(f"+{line}\n" for line in lines)
        diff += f"--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{added}"
    (directory / "changes.diff").write_text(diff, encoding="utf-8")
    return review


def test_value_set_table_shows_both_definitions_and_the_string_comparison(
    tmp_path: Path,
) -> None:
    review = _review(tmp_path, _synthetic_repo(tmp_path))
    figure = policy_value_sets.value_set_table(review)
    assert figure is not None
    row = next(row for row in figure.rows if "macro.py:1" in row.cells[0])
    assert "micro.py:1" in row.cells[0]
    assert "pipeline.py:2 等値" in row.cells[-2]


def test_cochange_table_has_the_strong_pair_and_not_the_weak_pair(tmp_path: Path) -> None:
    repo = _synthetic_repo(tmp_path)
    rows = cochange.count_co_changes(repo, frozenset({"micro.py"}))
    strong = next(r for r in rows if r.partner == "security.py")
    weak = next(r for r in rows if r.partner == "pipeline.py")
    assert strong.strength > cochange.MIN_STRENGTH
    # 共有の回数は足りるのに、強さが閾値に届かない対
    assert weak.shared_commits >= cochange.MIN_SHARED_COMMITS
    assert weak.strength < cochange.MIN_STRENGTH

    figure = policy_cochange.cochange_table(_review(tmp_path, repo))
    assert figure is not None
    pairs = {(row.cells[0], row.cells[1]): row.cells[-1] for row in figure.rows}
    assert pairs[("micro.py", "security.py")] == policy_cochange.StructuralDependency.ABSENT
    assert ("micro.py", "pipeline.py") not in pairs


def test_both_tables_keep_the_policy_prompts_under_the_input_limit(tmp_path: Path) -> None:
    review = _review(tmp_path, _synthetic_repo(tmp_path))
    prompts = principle_prompts(AssemblyInput.load(review))
    by_number = {item.principle.number: item.prompt for item in prompts}
    # 単一の情報源の原則の資料に、二つの表が載る
    assert f"### {policy_value_sets.SECTION_VALUE_SETS}" in by_number["P1"]
    assert f"### {policy_cochange.SECTION_COCHANGE}" in by_number["P1"]
    for number, prompt in by_number.items():
        principle = diagram_defaults().for_principle(number)
        assert not stops(principle, len(prompt.encode())), number
