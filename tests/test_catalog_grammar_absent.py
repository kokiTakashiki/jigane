"""組み込みの言語の文法が無いとき、flaky の目録とレビューの作成が止まらない試験。"""

from pathlib import Path

import pytest
from language_plugin_support import builtin_states_without_grammar

from jigane.flaky import catalog
from jigane.languages import registry
from jigane.reviews.lifecycle import create_feature_review


def test_selectable_names_include_grammar_absent_language() -> None:
    registry.set_states(builtin_states_without_grammar("swift"))
    assert "swift" in registry.selectable_names()
    assert "swift" not in {adapter.name for adapter in registry.adapters()}


def test_load_catalog_does_not_stop_when_a_builtin_grammar_is_absent() -> None:
    registry.set_states(builtin_states_without_grammar("swift"))
    items = catalog.load_catalog()
    assert any(spec.language == "swift" for item in items for spec in item.queries)


def test_feature_review_is_created_when_a_builtin_grammar_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "shop"
    (project / "src").mkdir(parents=True)
    (project / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (project / "src" / "B.swift").write_text("let x = 1\n", encoding="utf-8")
    registry.set_states(builtin_states_without_grammar("swift"))
    review = create_feature_review(project, ["src"], "p")
    assert review.repository_root
    assert registry.adapter_for("src/B.swift") is None
    assert registry.adapter_for("src/a.py") is not None
