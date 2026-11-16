from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue

from jigane.llm_runs.claude_schema import json_schema_of


class _None(BaseModel):
    reliance: Literal["none"]


class _Guess(BaseModel):
    reliance: Literal["guess"]


class _Docs(BaseModel):
    reliance: Literal["docs"]
    version: str = Field(min_length=1)


class _Run(BaseModel):
    reliance: Literal["run"]
    version: str = Field(min_length=1)


class _Claim(BaseModel):
    basis: Annotated[_None | _Guess | _Docs | _Run, Field(discriminator="reliance")]


def _contains_key(node: JsonValue, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_contains_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_contains_key(item, key) for item in node)
    return False


def _branches_by_reliance() -> dict[str, dict[str, JsonValue]]:
    properties = json_schema_of(_Claim)["properties"]
    assert isinstance(properties, dict)
    basis = properties["basis"]
    assert isinstance(basis, dict)
    branches = basis["oneOf"]
    assert isinstance(branches, list)
    result: dict[str, dict[str, JsonValue]] = {}
    for branch in branches:
        assert isinstance(branch, dict)
        branch_properties = branch["properties"]
        assert isinstance(branch_properties, dict)
        reliance = branch_properties["reliance"]
        assert isinstance(reliance, dict)
        name = reliance["const"]
        assert isinstance(name, str)
        result[name] = branch_properties
    return result


def test_discriminated_union_schema_has_no_discriminator_key() -> None:
    assert not _contains_key(json_schema_of(_Claim), "discriminator")


def test_discriminated_union_schema_keeps_four_branches() -> None:
    assert list(_branches_by_reliance()) == ["none", "guess", "docs", "run"]


def test_docs_and_run_branches_require_a_non_empty_version() -> None:
    by_reliance = _branches_by_reliance()
    for name in ("docs", "run"):
        version = by_reliance[name]["version"]
        assert isinstance(version, dict)
        assert version["minLength"] == 1
