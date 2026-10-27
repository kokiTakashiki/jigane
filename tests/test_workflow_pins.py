"""固定した参照を、構文木で確かめる。

外部の参照が commit SHA で固定されていることは、この試験が全ワークフローと composite action
（.github/**/action.yml）に当てて見る。zizmor の unpinned-uses は workflows 直下の *.yml
だけを見るため、action.yml の固定はここでしか確かめられない。引用した uses とフロー形式の uses
を落とすことは test_check_workflows.py が示す。行末の出所のコメントは、zizmor が見ない所なので
ここで見る。
"""

import re
from pathlib import Path

from workflow_yaml import load_yaml, trailing_comment, uses_holders

ROOT = Path(__file__).resolve().parent.parent
SHA = "a" * 40
PINNED = re.compile(r"[^@\s]+@[0-9a-f]{40}")


def workflow_files() -> list[Path]:
    github = ROOT / ".github"
    return sorted([*github.glob("workflows/*.yml"), *github.glob("**/action.yml")])


def uses_without_origin(text: str) -> list[str]:
    """出所のコメントが無い外部の uses を返す。"""
    return [
        value
        for holder, value, steps, index in uses_holders(load_yaml(text))
        # 同じリポジトリの中の action は、参照する側のコミットで中身が決まる。
        if not value.startswith("./") and not trailing_comment(holder, steps, index)
    ]


def uses_not_pinned(text: str) -> list[str]:
    """commit SHA で固定されていない外部の uses を返す。"""
    return [
        value
        for _, value, _, _ in uses_holders(load_yaml(text))
        if not value.startswith("./") and PINNED.fullmatch(value) is None
    ]


def test_workflows_exist() -> None:
    assert workflow_files()


def test_every_external_action_has_its_origin_in_a_trailing_comment() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}: {value}"
        for path in workflow_files()
        for value in uses_without_origin(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_every_external_uses_is_pinned_to_a_commit_sha() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}: {value}"
        for path in workflow_files()
        for value in uses_not_pinned(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_tag_pin_in_block_quoted_and_flow_forms_is_caught() -> None:
    block = "jobs:\n  a:\n    steps:\n      - uses: x/y@v1\n"
    quoted = "jobs:\n  a:\n    steps:\n      - uses: 'x/y@v1'\n"
    flow = "jobs:\n  a:\n    steps:\n      - {uses: x/y@v1}\n"
    assert uses_not_pinned(block) == ["x/y@v1"]
    assert uses_not_pinned(quoted) == ["x/y@v1"]
    assert uses_not_pinned(flow) == ["x/y@v1"]


def test_sha_pin_in_quoted_and_flow_forms_is_read_as_pinned() -> None:
    quoted = f"jobs:\n  a:\n    steps:\n      - uses: 'x/y@{SHA}'\n"
    flow = f"jobs:\n  a:\n    steps:\n      - {{uses: x/y@{SHA}}}\n"
    assert uses_not_pinned(quoted) == []
    assert uses_not_pinned(flow) == []


def test_composite_action_tag_pin_is_caught() -> None:
    composite = "runs:\n  using: composite\n  steps:\n    - uses: x/y@v1\n"
    assert uses_not_pinned(composite) == ["x/y@v1"]


def test_origin_in_block_form_is_read() -> None:
    text = f"jobs:\n  a:\n    steps:\n      - uses: x/y@{SHA} # v1.2.3\n"
    assert uses_without_origin(text) == []


def test_origin_in_flow_form_is_read() -> None:
    text = f"jobs:\n  a:\n    steps:\n      - {{uses: x/y@{SHA}}} # v1.2.3\n"
    assert uses_without_origin(text) == []


def test_quoted_uses_without_origin_is_caught() -> None:
    text = f"jobs:\n  a:\n    steps:\n      - uses: 'x/y@{SHA}'\n"
    assert uses_without_origin(text) == [f"x/y@{SHA}"]


def test_flow_uses_without_origin_is_caught() -> None:
    text = f"jobs:\n  a:\n    steps:\n      - {{uses: x/y@{SHA}}}\n"
    assert uses_without_origin(text) == [f"x/y@{SHA}"]


def test_comment_on_the_next_line_is_not_an_origin() -> None:
    text = (
        f"jobs:\n  a:\n    steps:\n      - uses: x/y@{SHA}\n        # v1.2.3\n        with: {{}}\n"
    )
    assert uses_without_origin(text) == [f"x/y@{SHA}"]


def test_comment_elsewhere_in_the_step_is_not_an_origin() -> None:
    text = (
        "jobs:\n  a:\n    steps:\n"
        f"      - name: n # v1.2.3\n        uses: x/y@{SHA}\n"
        "      - run: echo uses x/z@v1 # v2\n"
    )
    assert uses_without_origin(text) == [f"x/y@{SHA}"]


def test_reusable_workflow_and_composite_action_are_read() -> None:
    reusable = f"jobs:\n  a:\n    uses: x/y/.github/workflows/z.yml@{SHA}\n"
    composite = f"runs:\n  using: composite\n  steps:\n    - uses: x/y@{SHA}\n"
    assert uses_without_origin(reusable) == [f"x/y/.github/workflows/z.yml@{SHA}"]
    assert uses_without_origin(composite) == [f"x/y@{SHA}"]


def test_local_action_needs_no_origin() -> None:
    assert (
        uses_without_origin("jobs:\n  a:\n    steps:\n      - uses: ./.github/actions/x\n") == []
    )
