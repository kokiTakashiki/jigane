import importlib.util
from collections.abc import Callable
from pathlib import Path

from ruamel.yaml.comments import CommentedMap
from workflow_yaml import job_map, load_yaml

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
AUDIT = "uv run --locked python scripts/audit_dependencies.py"


def release_text() -> str:
    return RELEASE.read_text(encoding="utf-8")


def release() -> CommentedMap:
    return load_yaml(release_text())


def jobs() -> CommentedMap:
    return job_map(release())


def needs_of(job: CommentedMap) -> set[str]:
    """`needs` は文字列でも列でもよい。ジョブ名の集合に揃える。"""
    value = job.get("needs", [])
    return {value} if isinstance(value, str) else set(value)


def permission_holders(tree: CommentedMap, name: str, level: str = "write") -> set[str]:
    """権限 `name` を `level` で宣言したジョブの名前。コメントと run の文字列は数えない。"""
    return {
        job_name
        for job_name, job in job_map(tree).items()
        if isinstance(job.get("permissions"), dict) and job["permissions"].get(name) == level
    }


def environment_holders(tree: CommentedMap, environment: str) -> set[str]:
    return {
        job_name
        for job_name, job in job_map(tree).items()
        if job.get("environment") == environment
    }


def step_index(job: CommentedMap, matches: Callable[[CommentedMap], bool]) -> int:
    found = [index for index, step in enumerate(job.get("steps", [])) if matches(step)]
    assert len(found) == 1
    return found[0]


def trigger_of(text: str) -> object:
    spec = importlib.util.spec_from_file_location(
        "check_workflows", ROOT / "scripts" / "check_workflows.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return load_yaml(module.restore_commented_on(text))["on"]


def test_only_the_job_in_the_pypi_environment_holds_id_token() -> None:
    tree = release()
    assert permission_holders(tree, "id-token") == environment_holders(tree, "pypi") == {"publish"}


def test_only_the_publish_job_can_write_attestations() -> None:
    assert permission_holders(release(), "attestations") == {"publish"}


def test_top_level_permissions_grant_nothing() -> None:
    assert release()["permissions"] == {}


def test_publish_waits_for_verify_through_build() -> None:
    assert needs_of(jobs()["publish"]) == {"build"}
    assert needs_of(jobs()["build"]) == {"verify"}


def test_verify_audits_locked_dependencies_of_the_tagged_commit() -> None:
    verify = jobs()["verify"]
    tag = step_index(verify, lambda step: step.get("id") == "tag")
    checkout = step_index(
        verify, lambda step: step.get("with", {}).get("ref") == "${{ steps.tag.outputs.sha }}"
    )
    audit = step_index(verify, lambda step: step.get("run") == AUDIT)
    # 監査は、タグが指すと確かめたコミットの uv.lock に対して走らせる。
    assert tag < checkout < audit
    # 脆弱性が見つかったときに落ちるよう、失敗を握りつぶす指定を付けない。
    assert "continue-on-error" not in verify
    assert all("continue-on-error" not in step for step in verify.get("steps", []))


def test_restored_trigger_is_a_tag_push_only() -> None:
    assert trigger_of(release_text()) == {"push": {"tags": ["v*"]}}


def test_gh_token_never_reaches_steps_that_run_code_of_the_tag() -> None:
    verify = jobs()["verify"]
    # job 単位の env に置くと、タグの先のコードを走らせる段にも渡る。
    assert "GH_TOKEN" not in verify.get("env", {})
    checkout = step_index(
        verify, lambda step: step.get("with", {}).get("ref") == "${{ steps.tag.outputs.sha }}"
    )
    # タグの木をチェックアウトした段から後は、どの段にも env を置かない。
    assert all("env" not in step for step in verify.get("steps", [])[checkout:])


def test_github_release_job_runs_after_publish() -> None:
    assert needs_of(jobs()["github-release"]) == {"verify", "publish"}


def test_only_the_github_release_job_can_write_contents() -> None:
    assert permission_holders(release(), "contents") == {"github-release"}


def create_step_words() -> list[str]:
    """`gh release create` の段の run を、行の継続を外して語に分ける。改行の位置に頼らない。"""
    step = next(
        step
        for step in jobs()["github-release"].get("steps", [])
        if "gh release create" in step.get("run", "")
    )
    run = str(step["run"])
    command = run[run.index("gh release create") :]
    return command.replace("\\\n", " ").split("\n")[0].split()


def test_github_release_attaches_the_terms_the_privacy_policy_and_the_sbom() -> None:
    attached = {word for word in create_step_words() if word.endswith((".md", ".json"))}
    assert attached == {
        "TERMS.ja.md",
        "TERMS.en.md",
        "PRIVACY.ja.md",
        "PRIVACY.en.md",
        "artifacts/sbom.cdx.json",
    }


def test_github_release_requires_an_existing_tag() -> None:
    assert "--verify-tag" in create_step_words()


def test_github_release_checks_out_the_commit_verify_confirmed() -> None:
    job = jobs()["github-release"]
    checkout = job["steps"][
        step_index(job, lambda step: "actions/checkout@" in step.get("uses", ""))
    ]
    assert checkout.get("with", {}).get("ref") == "${{ needs.verify.outputs.sha }}"


def test_github_release_checks_the_tag_target_before_creating_the_release() -> None:
    job = jobs()["github-release"]
    check = step_index(job, lambda step: "VERIFIED_SHA" in step.get("env", {}))
    create = step_index(job, lambda step: "gh release create" in step.get("run", ""))
    body = str(job["steps"][check].get("run", ""))
    assert job["steps"][check]["env"]["VERIFIED_SHA"] == "${{ needs.verify.outputs.sha }}"
    assert body.index("target_sha=$(jq -r '.object.sha' <<<\"$tag_object\")") < body.index(
        '"$target_sha" != "$VERIFIED_SHA"'
    )
    assert check < create


def test_github_release_keeps_gh_token_out_of_the_job_env() -> None:
    job = jobs()["github-release"]
    assert "GH_TOKEN" not in job.get("env", {})
    assert any("GH_TOKEN" in step.get("env", {}) for step in job.get("steps", []))


# 以下は、行の読み方がすり抜けた書き方を、構文木の読み方が正しく扱うことを一時の例で確かめる。


def workflow_with(jobs_text: str) -> CommentedMap:
    return load_yaml("permissions: {}\njobs:\n" + jobs_text)


def test_id_token_in_a_comment_is_not_a_permission() -> None:
    tree = workflow_with(
        "  a:\n    permissions:\n      contents: read # id-token: write にはしない\n"
        "    steps:\n      - run: echo ok\n"
    )
    assert permission_holders(tree, "id-token") == set()


def test_id_token_in_a_run_string_is_not_a_permission() -> None:
    tree = workflow_with(
        "  a:\n    permissions:\n      contents: read\n    steps:\n"
        "      - run: |\n          echo 'id-token: write'\n"
    )
    assert permission_holders(tree, "id-token") == set()


def test_id_token_in_the_flow_form_is_a_permission() -> None:
    tree = workflow_with("  a:\n    permissions: {id-token: write}\n    steps:\n      - run: x\n")
    assert permission_holders(tree, "id-token") == {"a"}


def test_read_level_is_not_a_write_permission() -> None:
    tree = workflow_with(
        "  a:\n    permissions:\n      contents: read\n    steps:\n      - run: x\n"
    )
    assert permission_holders(tree, "contents") == set()


def test_needs_is_read_in_the_flow_form_and_the_scalar_form() -> None:
    tree = workflow_with(
        "  a:\n    needs: [b, c]\n    steps: []\n  d:\n    needs: 'b'\n    steps: []\n"
    )
    assert needs_of(job_map(tree)["a"]) == {"b", "c"}
    assert needs_of(job_map(tree)["d"]) == {"b"}


def test_a_commented_needs_is_not_a_dependency() -> None:
    tree = workflow_with("  a:\n    # needs: b\n    steps: []\n")
    assert needs_of(job_map(tree)["a"]) == set()


def test_a_trigger_beyond_the_tag_push_is_not_equal() -> None:
    text = "# on:\n#   push:\n#     tags: ['v*']\n#   workflow_dispatch:\nname: x\n"
    assert trigger_of(text) != {"push": {"tags": ["v*"]}}
    assert trigger_of("# on:\n#   push:\n#     tags: ['v*']\nname: x\n") == {
        "push": {"tags": ["v*"]}
    }
