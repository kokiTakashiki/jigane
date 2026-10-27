from pathlib import Path
from typing import Protocol

import pytest

from jigane.judgment_map.declaration import (
    DECLARATION_TEXT_LIMIT,
    EMPTY_BODY_WARNING,
    EMPTY_COMMITS_WARNING,
    EMPTY_INTENT_WARNING,
    NO_DECLARATION_WARNING,
    Declaration,
    DeclarationSource,
    commits_declaration,
    extract_declaration,
    intent_declaration,
    pull_request_declaration,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_nearest_readme_title_and_first_section(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Root\n\nroot lead\n\n## Other\n\nx\n")
    _write(
        tmp_path,
        "pkg/README.md",
        "# Pkg\n\nlead text\n\n## Usage\n\nuse it\n\n### Detail\n\nd\n\n## Next\n\nn\n",
    )
    _write(tmp_path, "pkg/a.py", "x = 1\n")
    texts = [e.text for e in extract_declaration(tmp_path, ["pkg/a.py"]).excerpts]
    assert texts[0] == "# Pkg\n\nlead text"
    assert texts[1] == "## Usage\n\nuse it\n\n### Detail\n\nd"
    assert all("root lead" not in text for text in texts)


def test_design_doc_section_mentioning_file_and_public_name(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "def build_widget():\n    return 1\n")
    _write(
        tmp_path,
        "docs/design.md",
        "# Design\n\n## One\n\nunrelated\n\n## Two\n\ncalls build_widget here\n",
    )
    excerpts = extract_declaration(tmp_path, ["a.py"]).excerpts
    assert [e.text for e in excerpts] == ["## Two\n\ncalls build_widget here"]
    assert excerpts[0].path == "docs/design.md"
    assert excerpts[0].start_line == 7


def test_short_names_do_not_match(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.py",
        "def run():\n    return 1\n\n\ndef build_widget():\n    return 1\n",
    )
    _write(tmp_path, "docs/d.md", "# D\n\n## S\n\nwe run it and rebuild_widgets\n")
    assert extract_declaration(tmp_path, ["a.py"]).excerpts == ()


def test_single_generic_word_does_not_match(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "class Review:\n    pass\n")
    _write(tmp_path, "docs/d.md", "# D\n\n## S\n\nthe Review of a Span\n")
    assert extract_declaration(tmp_path, ["a.py"]).excerpts == ()


def test_compound_names_and_file_name_match(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.py",
        "class ReviewId:\n    pass\n\n\ndef build_map():\n    return 1\n",
    )
    _write(
        tmp_path,
        "docs/d.md",
        "# D\n\n## Id\n\nuses ReviewId\n\n## Map\n\ncalls build_map\n\n"
        "## File\n\nsee a.py\n\n## None\n\nReview only\n",
    )
    headings = [e.text.splitlines()[0] for e in extract_declaration(tmp_path, ["a.py"]).excerpts]
    assert headings == ["## Id", "## Map", "## File"]


def test_empty_excerpts_are_reported_by_caller_warning_text() -> None:
    assert "M1" in NO_DECLARATION_WARNING


def test_intent_declaration_has_no_excerpts() -> None:
    declaration = intent_declaration("do the thing")
    assert declaration.warnings == ()
    assert declaration.intent == "do the thing"
    assert declaration.excerpts == ()


def test_empty_files_extracts_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Root\n\nlead\n\n## Sec\n\nbody\n")
    assert extract_declaration(tmp_path, []).excerpts == ()


class _Build(Protocol):
    def __call__(self, detail: str, *, thin: bool) -> Declaration: ...


def _pull_request(detail: str, *, thin: bool) -> Declaration:
    # 三つの源を同じ呼び方で扱うために受け取る。この源は薄さを detail から決めるので使わない
    del thin
    return pull_request_declaration("題", detail)


def _intent(detail: str, *, thin: bool) -> Declaration:
    # 三つの源を同じ呼び方で扱うために受け取る。この源は薄さを detail から決めるので使わない
    del thin
    return intent_declaration(detail)


def _commits(detail: str, *, thin: bool) -> Declaration:
    return commits_declaration(detail, has_body=not thin)


@pytest.mark.parametrize(
    ("build", "head", "thin_warning", "label"),
    [
        (_pull_request, "題\n\n", EMPTY_BODY_WARNING, "PR の本文"),
        (_intent, "", EMPTY_INTENT_WARNING, "利用者の文"),
        (_commits, "", EMPTY_COMMITS_WARNING, "範囲のコミットの文"),
    ],
    ids=["pull_request", "intent", "commits"],
)
def test_every_source_cuts_at_the_limit_and_warns_when_thin(
    build: _Build,
    head: str,
    thin_warning: str,
    label: str,
) -> None:
    exact = build("あ" * DECLARATION_TEXT_LIMIT, thin=False)
    assert exact.intent == head + "あ" * DECLARATION_TEXT_LIMIT
    assert exact.warnings == ()

    over = build("あ" * (DECLARATION_TEXT_LIMIT + 5), thin=False)
    assert over.intent == head + "あ" * DECLARATION_TEXT_LIMIT
    assert len(over.warnings) == 1
    assert label in over.warnings[0]
    assert "切った" in over.warnings[0]

    assert build("", thin=True).warnings == (thin_warning,)


@pytest.mark.parametrize("source", list(DeclarationSource))
def test_every_declaration_source_has_a_label_and_reads_back(source: DeclarationSource) -> None:
    declaration = Declaration(source, None, (), ("警告",))
    stored = declaration.to_dict()
    assert stored["source"] == source.value
    assert stored["source_label"] == source.label != ""
    assert Declaration.from_dict(stored) == declaration


def test_a_stored_source_string_is_read_as_the_enum() -> None:
    data = {"source": "pull_request", "intent": "x", "excerpts": [], "warnings": []}
    assert Declaration.from_dict(data).source is DeclarationSource.PULL_REQUEST


def test_only_the_users_own_text_is_kept_on_refresh() -> None:
    kept = {source for source in DeclarationSource if source.kept_on_refresh}
    assert kept == {DeclarationSource.INTENT}
