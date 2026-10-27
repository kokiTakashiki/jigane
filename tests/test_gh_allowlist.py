import ast
import os
from pathlib import Path

import pytest
from markdown_support import fences

from jigane.git import process
from jigane.git.environment import GIT_LOCATION_VARIABLES
from jigane.pull_request_context import gh
from jigane.pull_request_context.gh import GhCommandNotAllowedError

ROOT = Path(__file__).parent.parent
FIELDS = "number,url,title,body,baseRefOid,headRefOid,author"
ALLOWED = [
    ["gh", "pr", "view", "5", "-R", "o/r", "--json", FIELDS],
    ["gh", "repo", "clone", "o/r", "cache/x.git", "--", "--bare", "--filter=blob:none"],
    ["gh", "api", "--method", "GET", "--paginate", "--slurp", "repos/o/r/pulls/5/comments"],
]
REJECTED = [
    ["gh", "api", "-X", "POST", "repos/o/r/pulls/5/comments"],
    ["gh", "api", "--method", "POST", "repos/o/r/pulls/5/comments"],
    ["gh", "api", "--method=DELETE", "repos/o/r/pulls/5/comments"],
    ["gh", "api", "-f", "body=x", "repos/o/r/pulls/5/comments"],
    ["gh", "api", "-F", "body=x", "repos/o/r/pulls/5/comments"],
    ["gh", "api", "--field", "body=x", "repos/o/r/pulls/5/comments"],
    ["gh", "api", "--raw-field", "body=x", "repos/o/r/pulls/5/comments"],
    ["gh", "api", "--input", "x.json", "repos/o/r/pulls/5/comments"],
    [
        "gh",
        "api",
        "--method",
        "GET",
        "--paginate",
        "--slurp",
        "-XPOST",
        "repos/o/r/pulls/5/comments",
    ],
    ["gh", "api", "--method", "GET", "--paginate", "--slurp", "repos/o/r/issues/5/comments"],
    ["gh", "api", "--paginate", "--slurp", "repos/o/r/pulls/5/comments"],
    ["gh", "api", "--method=GET", "--paginate", "--slurp", "repos/o/r/pulls/5/comments"],
    ["gh", "api", "--method=POST", "--paginate", "--slurp", "repos/o/r/pulls/5/comments"],
    ["gh", "api", "-X", "GET", "--paginate", "--slurp", "repos/o/r/pulls/5/comments"],
    ["gh", "api", "--method", "POST", "--paginate", "--slurp", "repos/o/r/pulls/5/comments"],
    [
        "gh",
        "api",
        "--method",
        "GET",
        "--method",
        "POST",
        "--paginate",
        "repos/o/r/pulls/5/comments",
    ],
    ["gh", "pr", "merge", "5", "-R", "o/r"],
    ["gh", "pr", "view", "5", "-R", "o/r", "--json", "number"],
    ["gh", "pr", "view", "-5", "-R", "o/r", "--json", FIELDS],
    ["gh", "pr", "view", "0", "-R", "o/r", "--json", FIELDS],
    ["gh", "pr", "view", "5", "-R", "-o/r", "--json", FIELDS],
    ["gh", "pr", "view", "5", "-R", "o/-r", "--json", FIELDS],
    ["gh", "pr", "view", "5", "-R", "../r", "--json", FIELDS],
    ["gh", "pr", "view", "5", "-R", "o/r x", "--json", FIELDS],
    ["gh", "repo", "clone", "o/r", "--upload-pack=x", "--", "--bare", "--filter=blob:none"],
    ["gh", "repo", "clone", "o/r", "cache/x.git"],
    ["gh", "repo", "delete", "o/r"],
    ["gh", "auth", "token"],
    ["gh"],
    [],
    ["git", "status"],
]


class Launched:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[list[str]] = []
        self.environments: list[dict[str, str] | None] = []
        monkeypatch.setattr(process, "run_command", self._run)

    def _run(self, args: list[str], *_args: object, **kwargs: object) -> str:
        self.calls.append(args)
        self.environments.append(kwargs.get("env"))  # type: ignore[arg-type]
        return "{}"


@pytest.mark.parametrize("argv", ALLOWED)
def test_allowed_shapes_pass(argv: list[str]) -> None:
    gh.check_allowed(argv)


@pytest.mark.parametrize("argv", REJECTED)
def test_other_shapes_are_rejected(argv: list[str]) -> None:
    with pytest.raises(GhCommandNotAllowedError):
        gh.check_allowed(argv)


def test_functions_launch_exactly_the_allowed_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    launched = Launched(monkeypatch)
    gh.view_pull_request("o", "r", 5)
    gh.clone_repository("o", "r", "cache/x.git")
    gh.pull_request_comments("o", "r", 5)
    assert launched.calls == ALLOWED


def test_gh_is_pinned_to_github_com(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_HOST", "example.invalid")
    monkeypatch.setenv("GH_REPO", "o/x")
    monkeypatch.setenv("GH_TOKEN", "sentinel")
    launched = Launched(monkeypatch)
    gh.view_pull_request("o", "r", 5)
    gh.clone_repository("o", "r", "cache/x.git")
    gh.pull_request_comments("o", "r", 5)
    assert len(launched.environments) == 3
    for environment in launched.environments:
        assert environment is not None
        assert environment["GH_HOST"] == "github.com"
        assert "GH_REPO" not in environment
        assert environment["GH_TOKEN"] == os.environ["GH_TOKEN"]


@pytest.mark.parametrize(
    "call",
    [
        lambda: gh.view_pull_request("-o", "r", 5),
        lambda: gh.view_pull_request("o", "-r", 5),
        lambda: gh.view_pull_request("o", "r", -1),
        lambda: gh.view_pull_request("o", "r", 0),
        lambda: gh.clone_repository("-o", "r", "cache/x.git"),
        lambda: gh.clone_repository("o", "r", "--upload-pack=x"),
        lambda: gh.pull_request_comments("o", "r/../x", 5),
        lambda: gh.pull_request_comments("o", "r", -3),
    ],
)
def test_bad_arguments_are_rejected_before_gh_starts(
    monkeypatch: pytest.MonkeyPatch, call: object
) -> None:
    launched = Launched(monkeypatch)
    with pytest.raises(GhCommandNotAllowedError):
        call()  # type: ignore[operator]
    assert launched.calls == []


def test_rejection_maps_to_an_existing_failure_kind() -> None:
    from jigane.commands import failures  # noqa: PLC0415 循環を避けて試験の中で読む

    failure = failures.describe(GhCommandNotAllowedError("x"))
    assert failure.kind != failures.UNEXPECTED


def _starts_with_gh(node: ast.AST) -> bool:
    if not isinstance(node, ast.List | ast.Tuple) or not node.elts:
        return False
    first = node.elts[0]
    return isinstance(first, ast.Constant) and first.value == "gh"


def _gh_sequences(tree: ast.AST) -> list[ast.List | ast.Tuple]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.List | ast.Tuple) and _starts_with_gh(node)
    ]


def test_gh_argv_is_built_only_in_the_gh_module() -> None:
    allowed_module = ROOT / "jigane" / "pull_request_context" / "gh.py"
    offenders = []
    for path in sorted((ROOT / "jigane").rglob("*.py")):
        if path == allowed_module:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [f"{path.relative_to(ROOT)}:{node.lineno}" for node in _gh_sequences(tree)]
    assert offenders == []


def test_allowlist_matches_readme() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    blocks = fences(readme, "## jigane が呼ぶ gh のコマンド")
    listed = [
        block.content.removeprefix("gh ").rstrip("\n")
        for block in blocks
        if block.info == "bash" and block.content.startswith("gh ")
    ]
    assert sorted(listed) == sorted(" ".join(shape) for shape in gh.ALLOWED_SHAPES)


def test_gh_child_drops_the_git_location_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    # gh repo clone は中で git を呼ぶので、親から受け継いだ場所の変数が clone の向き先を狂わせない
    for variable in GIT_LOCATION_VARIABLES:
        monkeypatch.setenv(variable, "/elsewhere")
    monkeypatch.setenv("GH_TOKEN", "sentinel")
    launched = Launched(monkeypatch)
    gh.clone_repository("o", "r", "cache/x.git")
    (environment,) = launched.environments
    assert environment is not None
    assert not set(GIT_LOCATION_VARIABLES) & set(environment)
    assert environment["GH_TOKEN"] == os.environ["GH_TOKEN"]
