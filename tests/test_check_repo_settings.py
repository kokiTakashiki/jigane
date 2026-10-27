import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from workflow_yaml import load_yaml, uses_values

ROOT = Path(__file__).resolve().parent.parent


def load_checker() -> ModuleType:
    path = ROOT / "scripts" / "check_repo_settings.py"
    spec = importlib.util.spec_from_file_location("check_repo_settings", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load_checker()
SETTINGS = json.loads(checker.SETTINGS_PATH.read_text(encoding="utf-8"))


def build_responses(profile: str) -> dict[str, Any]:
    # 期待の一覧から、期待どおりの応答を組み立てる
    responses: dict[str, Any] = {}
    for endpoint, items in SETTINGS[profile].items():
        body: Any = {}
        arrays: dict[str, dict[str, list[Any]]] = {}
        for path, value in items.items():
            if "[]." in path:
                # 配列の応答（ルールの一覧など）。期待の値ごとに要素を一つ並べる
                head, _, key = path.rpartition("[].")
                arrays.setdefault(head, {})[key] = value if isinstance(value, list) else [value]
                continue
            node = body
            *parents, leaf = path.split(".")
            for parent in parents:
                node = node.setdefault(parent, {})
            node[leaf] = value
        for head, keys in arrays.items():
            elements = [
                {key: values[index] for key, values in keys.items()}
                for index in range(max(len(values) for values in keys.values()))
            ]
            if not head:
                body = elements
                continue
            node = body
            *parents, leaf = head.split(".")
            for parent in parents:
                node = node.setdefault(parent, {})
            node[leaf] = elements
        responses[endpoint.format(repo="o/r")] = body
    return responses


def reader(responses: dict[str, Any]) -> Callable[[str], object]:
    def read(endpoint: str) -> object:
        result = responses[endpoint]
        if isinstance(result, Exception):
            raise result
        return result

    return read


@pytest.mark.parametrize("profile", ["working", "public"])
def test_matching_settings_pass(profile: str) -> None:
    code = checker.main(["--repo", "o/r", "--profile", profile], reader(build_responses(profile)))
    assert code == 0


def test_one_difference_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    responses = build_responses("public")
    responses["repos/o/r"]["allow_forking"] = False
    code = checker.main(["--repo", "o/r", "--profile", "public"], reader(responses))
    assert code == 1
    assert capsys.readouterr().err.splitlines() == [
        "repos/o/r: allow_forking: 期待 True、実際 False"
    ]


def test_nested_difference_is_reported() -> None:
    responses = build_responses("public")
    responses["repos/o/r"]["security_and_analysis"]["secret_scanning"]["status"] = "disabled"
    differences = checker.find_differences(SETTINGS["public"], "o/r", reader(responses))
    assert differences == [
        "repos/o/r: security_and_analysis.secret_scanning.status: 期待 'enabled'、実際 'disabled'"
    ]


def test_unreadable_endpoint_counts_as_differences() -> None:
    responses = build_responses("public")
    endpoint = "repos/o/r/private-vulnerability-reporting"
    responses[endpoint] = checker.CommandError(["gh", "api", endpoint], "gh: Not Found (HTTP 404)")
    differences = checker.find_differences(SETTINGS["public"], "o/r", reader(responses))
    assert len(differences) == 1
    assert differences[0].startswith(f"{endpoint}: enabled: 期待 True、実際 読めない")
    assert "404" in differences[0]


def test_missing_key_counts_as_difference() -> None:
    responses = build_responses("working")
    del responses["repos/o/r/actions/permissions"]["enabled"]
    differences = checker.find_differences(SETTINGS["working"], "o/r", reader(responses))
    assert differences == ["repos/o/r/actions/permissions: enabled: 期待 True、実際 応答に無い"]


def test_array_value_is_compared_by_equality() -> None:
    endpoint = "repos/o/r/actions/permissions/selected-actions"
    expected = {endpoint.replace("o/r", "{repo}"): {"patterns_allowed": ["astral-sh/setup-uv@*"]}}
    same = {endpoint: {"patterns_allowed": ["astral-sh/setup-uv@*"]}}
    assert checker.find_differences(expected, "o/r", reader(same)) == []
    for actual in (["other/*"], ["astral-sh/setup-uv@*", "other/*"], []):
        differences = checker.find_differences(
            expected, "o/r", reader({endpoint: {"patterns_allowed": actual}})
        )
        assert differences == [
            f"{endpoint}: patterns_allowed: 期待 ['astral-sh/setup-uv@*']、実際 {actual!r}"
        ]


SIGNATURE_ENDPOINTS = ["repos/o/r/rules/branches/main", "repos/o/r/rules/tags"]


@pytest.mark.parametrize("endpoint", SIGNATURE_ENDPOINTS)
def test_missing_required_signatures_is_a_difference(endpoint: str) -> None:
    responses = build_responses("public")
    responses[endpoint] = [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}]
    differences = checker.find_differences(SETTINGS["public"], "o/r", reader(responses))
    wanted = "期待 'required_signatures' を含む、実際 ['creation', 'update', 'deletion']"
    assert differences == [f"{endpoint}: [].type: {wanted}"]


@pytest.mark.parametrize("endpoint", SIGNATURE_ENDPOINTS)
def test_empty_rule_list_is_a_difference(endpoint: str) -> None:
    responses = build_responses("public")
    responses[endpoint] = []
    differences = checker.find_differences(SETTINGS["public"], "o/r", reader(responses))
    wanted = SETTINGS["public"][endpoint.replace("o/r", "{repo}")]["[].type"]
    wanted_rules = wanted if isinstance(wanted, list) else [wanted]
    assert differences == [
        f"{endpoint}: [].type: 期待 {rule!r} を含む、実際 []" for rule in wanted_rules
    ]


def test_public_expects_alerts_on_and_security_updates_off() -> None:
    responses = build_responses("public")
    responses["repos/o/r/vulnerability-alerts"] = {"enabled": False}
    responses["repos/o/r/automated-security-fixes"] = {"enabled": True, "paused": False}
    differences = checker.find_differences(SETTINGS["public"], "o/r", reader(responses))
    assert differences == [
        "repos/o/r/vulnerability-alerts: enabled: 期待 True、実際 False",
        "repos/o/r/automated-security-fixes: enabled: 期待 False、実際 True",
    ]


TAG_RULES = "repos/o/r/rules/tags"
TAG_SCOPE = "repos/o/r/rulesets/tag-scope"
ENVIRONMENT = "repos/o/r/environments/pypi"
ENVIRONMENT_POLICIES = "repos/o/r/environments/pypi/deployment-branch-policies"
TESTPYPI_ENVIRONMENT = "repos/o/r/environments/testpypi"
TESTPYPI_ENVIRONMENT_POLICIES = "repos/o/r/environments/testpypi/deployment-branch-policies"


@pytest.mark.parametrize("missing", ["creation", "update", "deletion"])
def test_tag_ruleset_must_restrict_creation_update_and_deletion(missing: str) -> None:
    responses = build_responses("public")
    responses[TAG_RULES] = [
        {"type": rule}
        for rule in ("required_signatures", "creation", "update", "deletion")
        if rule != missing
    ]
    differences = checker.find_differences(SETTINGS["public"], "o/r", reader(responses))
    assert len(differences) == 1
    assert differences[0].startswith(f"{TAG_RULES}: [].type: 期待 {missing!r} を含む")


@pytest.mark.parametrize(
    ("key", "actual"),
    [
        ("bypass_actors", ["RepositoryRole:5", "Team:9"]),
        ("bypass_actors", []),
        ("include", ["refs/tags/*"]),
        ("include", []),
        ("exclude", ["refs/tags/v1*"]),
    ],
)
def test_tag_scope_difference_is_reported(key: str, actual: list[str]) -> None:
    responses = build_responses("public")
    responses[TAG_SCOPE][key] = actual
    differences = checker.find_differences(SETTINGS["public"], "o/r", reader(responses))
    wanted = SETTINGS["public"]["repos/{repo}/rulesets/tag-scope"][key]
    assert differences == [f"{TAG_SCOPE}: {key}: 期待 {wanted!r}、実際 {actual!r}"]


def test_pypi_environment_expects_reviewers_and_no_admin_bypass() -> None:
    assert_environment_expects_reviewers_and_no_admin_bypass(ENVIRONMENT)


def test_testpypi_environment_expects_reviewers_and_no_admin_bypass() -> None:
    assert_environment_expects_reviewers_and_no_admin_bypass(TESTPYPI_ENVIRONMENT)


def assert_environment_expects_reviewers_and_no_admin_bypass(endpoint: str) -> None:
    responses = build_responses("public")
    environment = responses[endpoint]
    assert environment["can_admins_bypass"] is False
    environment["can_admins_bypass"] = True
    environment["protection_rules"] = [{"type": "branch_policy"}]
    environment["deployment_branch_policy"]["custom_branch_policies"] = False
    differences = checker.find_differences(SETTINGS["public"], "o/r", reader(responses))
    reviewers_missing = "期待 'required_reviewers' を含む、実際 ['branch_policy']"
    assert differences == [
        f"{endpoint}: can_admins_bypass: 期待 False、実際 True",
        f"{endpoint}: protection_rules[].type: {reviewers_missing}",
        f"{endpoint}: deployment_branch_policy.custom_branch_policies: 期待 True、実際 False",
    ]


@pytest.mark.parametrize(
    "policies",
    [
        [{"name": "v*", "type": "tag"}, {"name": "main", "type": "branch"}],
        [{"name": "main", "type": "branch"}],
        [{"name": "*", "type": "tag"}],
    ],
)
def test_pypi_deployment_must_be_tag_v_star_only(policies: list[dict[str, str]]) -> None:
    responses = build_responses("public")
    responses[ENVIRONMENT_POLICIES] = {"total_count": len(policies), "branch_policies": policies}
    differences = checker.find_differences(SETTINGS["public"], "o/r", reader(responses))
    assert differences != []
    assert all(difference.startswith(f"{ENVIRONMENT_POLICIES}: ") for difference in differences)


@pytest.mark.parametrize(
    "policies",
    [
        [{"name": "main", "type": "branch"}, {"name": "v*", "type": "tag"}],
        [{"name": "v*", "type": "tag"}],
        [{"name": "*", "type": "branch"}],
    ],
)
def test_testpypi_deployment_must_be_main_branch_only(policies: list[dict[str, str]]) -> None:
    responses = build_responses("public")
    responses[TESTPYPI_ENVIRONMENT_POLICIES] = {
        "total_count": len(policies),
        "branch_policies": policies,
    }
    differences = checker.find_differences(SETTINGS["public"], "o/r", reader(responses))
    assert differences != []
    assert all(
        difference.startswith(f"{TESTPYPI_ENVIRONMENT_POLICIES}: ") for difference in differences
    )


def test_tag_scope_collects_include_and_bypass_of_active_tag_rulesets() -> None:
    summaries = [
        {"id": 1, "target": "tag", "enforcement": "disabled"},
        {"id": 2, "target": "tag", "enforcement": "active"},
    ]
    detail = {
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": ["refs/tags/v1*"]}},
        "bypass_actors": [
            {"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always"}
        ],
        "rules": [],
    }
    scope = checker.collect_tag_scope(
        "o/r", reader({"repos/o/r/rulesets/2": detail}), lambda _: [summaries]
    )
    assert scope == {
        "include": ["refs/tags/v*"],
        "exclude": ["refs/tags/v1*"],
        "bypass_actors": ["RepositoryRole:5"],
    }


def test_working_profile_has_no_signature_expectation() -> None:
    assert not any("rules" in endpoint for endpoint in SETTINGS["working"])


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_working_profile_expects_the_public_workflows_disabled(workflow: str) -> None:
    endpoint = f"repos/{{repo}}/actions/workflows/{workflow}"
    assert SETTINGS["working"][endpoint] == {"state": "disabled_manually"}


def test_tag_rules_come_from_active_tag_rulesets_only() -> None:
    summaries = [
        {"id": 1, "target": "branch", "enforcement": "active"},
        {"id": 2, "target": "tag", "enforcement": "evaluate"},
        {"id": 3, "target": "tag", "enforcement": "active"},
    ]
    responses: dict[str, Any] = {
        "repos/o/r/rulesets/3": {"rules": [{"type": "required_signatures"}]},
    }
    rules = checker.collect_tag_rules("o/r", reader(responses), lambda _: [summaries])
    assert rules == [{"type": "required_signatures"}]


def test_tag_ruleset_on_a_later_page_is_collected() -> None:
    pages = [
        [{"id": 1, "target": "branch", "enforcement": "active"}],
        [{"id": 2, "target": "tag", "enforcement": "active"}],
    ]
    responses: dict[str, Any] = {"repos/o/r/rulesets/2": {"rules": [{"type": "deletion"}]}}
    rules = checker.collect_tag_rules("o/r", reader(responses), lambda _: pages)
    assert rules == [{"type": "deletion"}]


@pytest.mark.parametrize(
    "pages",
    [
        {"id": 1},
        [{"id": 1}],
        [["not a dict"]],
        [[{"id": 1, "target": "tag"}]],
    ],
)
def test_malformed_ruleset_list_is_a_difference(pages: object) -> None:
    def read(endpoint: str) -> object:
        return (
            {}
            if endpoint != "repos/o/r/rules/tags"
            else checker.collect_tag_rules("o/r", reader({}), lambda _: pages)
        )

    expected = {"repos/{repo}/rules/tags": {"[].type": "required_signatures"}}
    differences = checker.find_differences(expected, "o/r", read)
    assert len(differences) == 1
    assert "応答の形が違う" in differences[0]


def test_read_api_routes_tag_scope_to_the_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checker, "collect_tag_scope", lambda repo, _read: {"repo": [repo]})
    assert checker.read_api("repos/o/r/rulesets/tag-scope") == {"repo": ["o/r"]}


def test_read_api_routes_tag_rules_to_the_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def collect(repo: str, _read: object) -> list[object]:
        seen.append(repo)
        return [{"type": "required_signatures"}]

    monkeypatch.setattr(checker, "collect_tag_rules", collect)
    assert checker.read_api("repos/o/r/rules/tags") == [{"type": "required_signatures"}]
    assert seen == ["o/r"]


def test_empty_body_means_enabled_only_for_vulnerability_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(checker, "run_command", lambda _: "")
    assert checker.read_gh("repos/o/r/vulnerability-alerts") == {"enabled": True}
    for endpoint in (
        "repos/o/r/private-vulnerability-reporting",
        "repos/o/r/automated-security-fixes",
    ):
        with pytest.raises(json.JSONDecodeError):
            checker.read_gh(endpoint)


SELECTED_ACTIONS = "repos/o/r/actions/permissions/selected-actions"
SELECTED_ACTIONS_TEMPLATE = "repos/{repo}/actions/permissions/selected-actions"
PUBLIC_WORKFLOWS = ("ci.yml", "release.yml")


def test_public_selected_actions_match_passes() -> None:
    responses = build_responses("public")
    assert SELECTED_ACTIONS in responses
    assert checker.find_differences(SETTINGS["public"], "o/r", reader(responses)) == []


@pytest.mark.parametrize(
    "patterns",
    [
        ["astral-sh/setup-uv@*"],
        [
            "astral-sh/setup-uv@*",
            "astral-sh/attest-action@*",
            "example/extra-action@*",
        ],
    ],
)
def test_public_selected_actions_difference_is_reported(patterns: list[str]) -> None:
    responses = build_responses("public")
    responses[SELECTED_ACTIONS]["patterns_allowed"] = patterns
    differences = checker.find_differences(SETTINGS["public"], "o/r", reader(responses))
    wanted = SETTINGS["public"][SELECTED_ACTIONS_TEMPLATE]["patterns_allowed"]
    assert differences == [
        f"{SELECTED_ACTIONS}: patterns_allowed: 期待 {wanted!r}、実際 {patterns!r}"
    ]


def third_party_actions(text: str) -> set[str]:
    """構文木の `uses` の値から、`@` の前の action 名を集める。actions/ 以外だけを返す。"""
    used = set()
    for value in uses_values(load_yaml(text)):
        action, at, _ = value.partition("@")
        # 同じリポジトリの action（./）と docker:// は、許可一覧の対象でない。
        if at and not action.startswith(("./", "docker://")):
            used.add(action)
    return {action for action in used if not action.startswith("actions/")}


def public_workflow_third_party_uses() -> set[str]:
    used: set[str] = set()
    for name in PUBLIC_WORKFLOWS:
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        used |= third_party_actions(text)
    return used


def expected_actions(expected: dict[str, Any]) -> set[str]:
    return {pattern.removesuffix("@*") for pattern in expected["patterns_allowed"]}


def unused_unexpected_actions(allowed: set[str], used: set[str]) -> set[str]:
    return allowed - used


def test_public_selected_actions_allow_every_public_workflow_use() -> None:
    expected = SETTINGS["public"][SELECTED_ACTIONS_TEMPLATE]
    used = public_workflow_third_party_uses()
    assert used <= expected_actions(expected)
    assert expected["github_owned_allowed"] is True


def test_public_selected_actions_have_no_unused_entries() -> None:
    expected = SETTINGS["public"][SELECTED_ACTIONS_TEMPLATE]
    used = public_workflow_third_party_uses()
    assert unused_unexpected_actions(expected_actions(expected), used) == set()
    stray = {*expected_actions(expected), "example/unused-action"}
    assert unused_unexpected_actions(stray, used) == {"example/unused-action"}


def steps_of(*lines: str) -> str:
    return "jobs:\n  a:\n    steps:\n" + "".join(f"      {line}\n" for line in lines)


def test_third_party_uses_ignores_a_uses_in_a_comment_and_a_string() -> None:
    text = steps_of(
        "# - uses: evil/commented@v1",
        "- run: |",
        "    echo 'uses: evil/quoted@v1'",
        "- uses: good/real@v1",
    )
    assert third_party_actions(text) == {"good/real"}


def test_third_party_uses_collects_the_quoted_and_the_flow_forms() -> None:
    text = steps_of("- uses: 'q/quoted@v1'", '- uses: "d/double@v1"', "- {uses: f/flow@v1}")
    assert third_party_actions(text) == {"q/quoted", "d/double", "f/flow"}


def test_third_party_uses_collects_a_reusable_workflow_job() -> None:
    text = "jobs:\n  a:\n    uses: o/r/.github/workflows/w.yml@v1\n"
    assert third_party_actions(text) == {"o/r/.github/workflows/w.yml"}


def test_third_party_uses_skips_github_owned_and_local_actions() -> None:
    text = steps_of("- uses: actions/checkout@v1", "- uses: ./local/action")
    assert third_party_actions(text) == set()
