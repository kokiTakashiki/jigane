import importlib.util
from email.message import Message
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_checker() -> ModuleType:
    path = ROOT / "scripts" / "check_dependency_licenses.py"
    spec = importlib.util.spec_from_file_location("check_dependency_licenses", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def headers(**fields: str) -> Message:
    message = Message()
    for name, value in fields.items():
        message[name.replace("_", "-")] = value
    return message


def classifier_headers(*values: str) -> Message:
    message = Message()
    for value in values:
        message["Classifier"] = value
    return message


@pytest.mark.parametrize(
    "message",
    [
        headers(License_Expression="MIT"),
        headers(License_Expression="MPL-2.0"),
        headers(License_Expression="Apache-2.0 AND MIT"),
        headers(License="BSD License"),
        headers(License="Apache Software License"),
        classifier_headers("License :: OSI Approved :: MIT License"),
        classifier_headers("License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)"),
    ],
)
def test_allowed_license_passes(message: Message) -> None:
    assert load_checker().license_violation(message) is None


@pytest.mark.parametrize(
    "message",
    [
        headers(License_Expression="GPL-3.0-only"),
        headers(License_Expression="MIT OR AGPL-3.0-or-later"),
        headers(License="GNU Affero General Public License v3"),
        classifier_headers("License :: OSI Approved :: GNU General Public License v3 (GPLv3)"),
        headers(Name="no-license"),
    ],
)
def test_disallowed_or_missing_license_fails(message: Message) -> None:
    assert load_checker().license_violation(message) is not None


def test_pasted_license_text_falls_through_to_classifier() -> None:
    message = headers(License="Permission is hereby granted\nfree of charge")
    message["Classifier"] = "License :: OSI Approved :: MIT License"

    assert load_checker().license_violation(message) is None


def test_runtime_dependencies_are_collected_without_dev_tools() -> None:
    names = set(load_checker().runtime_distributions())

    assert {
        "jigane",
        "tree-sitter",
        "pydantic",
        "pathspec",
        "jinja2",
        "markupsafe",
    } <= names
    assert "pytest" not in names
    assert "jsonschema" not in names
    assert "ruff" not in names


def test_every_installed_runtime_dependency_is_allowed() -> None:
    results = load_checker().check()

    assert results
    assert {name: reason for name, reason in results.items() if reason} == {}
