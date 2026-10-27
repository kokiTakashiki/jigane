from jigane.languages.registry import (
    adapter_for,
    adapters,
    is_build_path,
    is_test_path,
)


def test_adapter_for_known_suffix() -> None:
    adapter = adapter_for("src/a.py")
    assert adapter is not None
    assert adapter.name == "python"


def test_adapter_for_unknown_suffix() -> None:
    assert adapter_for("a.unknown") is None
    assert adapter_for("Makefile") is None


def test_test_paths() -> None:
    registered = adapters()
    assert is_test_path("src/a/tests/x.py", registered)
    assert is_test_path("test_x.py", registered)
    assert is_test_path("pkg/x_test.py", registered)
    assert is_test_path("pkg/conftest.py", registered)
    assert not is_test_path("src/a.py", registered)


def test_build_paths() -> None:
    registered = adapters()
    assert is_build_path("pyproject.toml", registered)
    assert is_build_path("requirements-dev.txt", registered)
    assert is_build_path("sub/setup.py", registered)
    assert not is_build_path("sub/uv.lock", registered)
    for common in (
        "Makefile",
        "sub/Makefile",
        "rules.mk",
        "a/justfile",
        "docker/Dockerfile",
        ".github/workflows/ci.yml",
        ".github/workflows/ci.yaml",
    ):
        assert is_build_path(common, registered), common
    assert not is_build_path(".github/CODEOWNERS", registered)
    assert not is_build_path("docs/Makefile.md", registered)
    assert not is_build_path("src/a.py", registered)
