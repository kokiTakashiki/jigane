"""一覧の行の項目が、どの書き込みで変わるかを実物の CLI で確かめ、書き込みの表と突き合わせる。"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pr_support import URL, grow_head

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.reviews.lifecycle import Review, create_feature_review, create_pull_request_review

ROW_FIELDS = {
    "id",
    "repository",
    "key",
    "kind",
    "mode",
    "is_diff",
    "is_pull_request",
    "title",
    "range_text",
    "design_doc",
    "created_at",
    "required_lines",
    "total_lines",
    "file_count",
    "archived",
}


def _finding(number: int) -> dict[str, Any]:
    return {
        "id": f"F{number}",
        "severity": "warning",
        "group": "micro",
        "viewpoint": "D1",
        "claim": f"claim{number}",
        "evidence": [{"path": "src/a.py", "start_line": 1, "end_line": 2}],
        "adjudication": {
            "kind": "rebuttal",
            "challenge": "c",
            "recheck": [],
            "verdict": "kept",
            "reason": "r",
        },
        "theme": None,
        "triage": "must_read",
        "judgment_region": None,
    }


@pytest.fixture
def review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n" + "x = 1\n" * 50, encoding="utf-8")
    monkeypatch.chdir(root)
    created = create_feature_review(root, ["src"], "p")
    state = {
        "last_number": 2,
        "last_theme_number": 0,
        "stale_groups": [],
        "groups": {"micro": [_finding(1)]},
        "themes": [],
    }
    (created.id.directory() / "findings.json").write_text(json.dumps(state), encoding="utf-8")
    return created


@pytest.fixture
def pr_review(
    pr: dict[str, Any],  # noqa: ARG001 - pr フィクスチャの偽物を先に差し込むための依存で、値は使わない
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Review:
    # 偽の PR の取り込み（pr フィクスチャ）で作る差分レビュー式。作業場所は PR と無関係な場所へ移す
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
    monkeypatch.chdir(root)
    created = create_pull_request_review(URL)
    finding = _finding(1)
    finding["evidence"] = [{"path": "edit.py", "start_line": 1, "end_line": 2}]
    state = {
        "last_number": 2,
        "last_theme_number": 0,
        "stale_groups": [],
        "groups": {"micro": [finding]},
        "themes": [],
    }
    (created.id.directory() / "findings.json").write_text(json.dumps(state), encoding="utf-8")
    return created


def _row(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    # アーカイブ済みの行は通常の一覧から外れるので、両方を引く
    assert main(["list", "--json"]) == EXIT_OK
    rows = json.loads(capsys.readouterr().out)
    assert main(["list", "--archived", "--json"]) == EXIT_OK
    rows += json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    row: dict[str, Any] = rows[0]
    assert set(row) == ROW_FIELDS
    return row


def _changed_fields(argv: list[str], capsys: pytest.CaptureFixture[str]) -> set[str]:
    before = _row(capsys)
    assert main(argv) in (EXIT_OK, 3), argv
    capsys.readouterr()
    after = _row(capsys)
    return {name for name in ROW_FIELDS if before[name] != after[name]}


def test_list_row_changes_match_the_row_fields(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path.cwd()

    def grow_source() -> None:
        (root / "src" / "b.py").write_text("def widget():\n    return 2\n", encoding="utf-8")

    refreshed = {"file_count", "required_lines", "total_lines"}
    _compare_row_changes(review, capsys, grow_source, refreshed)


def test_list_row_changes_match_the_row_fields_for_diff_review(
    pr_review: Review, pr: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    # PR の情報を取り直す refresh は、先の commit が進まなければ何も変えない
    assert _changed_fields(["refresh", str(pr_review.id), "--json"], capsys) == set()
    # 偽の取り込みの先の commit を進めると、ファイル数と行数が変わる
    _compare_row_changes(
        pr_review, capsys, lambda: grow_head(pr), {"file_count", "required_lines", "total_lines"}
    )


def _compare_row_changes(
    review: Review,
    capsys: pytest.CaptureFixture[str],
    grow_source: Callable[[], None],
    refreshed: set[str],
) -> None:
    target = str(review.id)

    observed: dict[str, set[str]] = {}
    observed["pick"] = _changed_fields(["pick", target, "F1", "--json"], capsys)
    observed["hold"] = _changed_fields(["hold", target, "F1", "--json"], capsys)
    observed["object"] = _changed_fields(
        ["object", target, "F1", "--reason", "no", "--json"], capsys
    )
    observed["unobject"] = _changed_fields(["unobject", target, "F1", "--json"], capsys)
    grow_source()
    observed["refresh"] = _changed_fields(["refresh", target, "--json"], capsys)
    observed["archive"] = _changed_fields(["archive", target, "--json"], capsys)
    observed["unarchive"] = _changed_fields(["unarchive", target, "--json"], capsys)

    # 選択に触る書き込みは行を変えない。取り込みは行数とファイル数を、
    # アーカイブは archived を変える
    assert (
        observed["pick"] == observed["hold"] == observed["object"] == observed["unobject"] == set()
    )
    assert observed["refresh"] == refreshed
    assert observed["archive"] == observed["unarchive"] == {"archived"}


def test_list_archived_marks_archived_rows_in_the_text_output(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    target = str(review.id)
    assert main(["list"]) == EXIT_OK
    assert "アーカイブ済み" not in capsys.readouterr().out
    assert main(["archive", target, "--json"]) == EXIT_OK
    capsys.readouterr()
    assert main(["list"]) == EXIT_OK
    assert target not in capsys.readouterr().out
    assert main(["list", "--archived"]) == EXIT_OK
    assert "[アーカイブ済み]" in capsys.readouterr().out
