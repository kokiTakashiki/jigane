import json
from pathlib import Path

import pytest
from sarif_support import log, result, run, write
from test_static_findings_import import MESSAGE, _request, _sarif, repo  # noqa: F401 pytest が fixture を名前で引くため import が要る

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.reviews.lifecycle import DeclaredInputs, create_range_review
from jigane.storage.store import ReviewId


def _with_sarif_review(repo: dict[str, str]) -> ReviewId:  # noqa: F811 import した fixture の値を受ける
    root = Path(repo["root"])
    path = _sarif(root, "a.sarif", revision=repo["head"])
    return create_range_review(
        root, "main..feature", "mine", DeclaredInputs(sarif=_request(root, path))
    ).id


def test_show_prints_the_counts_and_the_finding_message(
    repo: dict[str, str],  # noqa: F811 import した fixture を引数で受けて注入させる
    capsys: pytest.CaptureFixture[str],
) -> None:
    review_id = _with_sarif_review(repo)
    capsys.readouterr()
    assert main(["show", str(review_id)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "静的検査の指摘: 1 件" in out
    assert "上げた判断の箇所: 1 件、差分の外: 0 件" in out
    assert "捨てた指摘: 0 件" in out
    assert "S1 ruff" in out
    assert MESSAGE in out


def test_show_json_carries_the_static_findings_field(
    repo: dict[str, str],  # noqa: F811 import した fixture を引数で受けて注入させる
    capsys: pytest.CaptureFixture[str],
) -> None:
    review_id = _with_sarif_review(repo)
    capsys.readouterr()
    assert main(["show", str(review_id), "--json"]) == EXIT_OK
    field = json.loads(capsys.readouterr().out)["static_findings"]
    assert field["summary"] == {
        "finding_count": 1,
        "raised_region_count": 1,
        "outside_diff_count": 0,
        "dropped": {},
    }
    assert field["findings"][0]["id"] == "S1"
    assert field["findings"][0]["message"] == MESSAGE
    assert [segment["finding_ids"] for segment in field["segments"]] == [["S1"]]


def test_show_without_sarif_has_no_field_and_no_line(
    repo: dict[str, str],  # noqa: F811 import した fixture を引数で受けて注入させる
    capsys: pytest.CaptureFixture[str],
) -> None:
    review = create_range_review(Path(repo["root"]), "main..feature", "mine")
    capsys.readouterr()
    assert main(["show", str(review.id), "--json"]) == EXIT_OK
    assert "static_findings" not in json.loads(capsys.readouterr().out)
    assert main(["show", str(review.id)]) == EXIT_OK
    assert "静的検査の指摘" not in capsys.readouterr().out


def test_show_counts_outside_and_dropped_and_keeps_one_line_per_finding(
    repo: dict[str, str],  # noqa: F811 import した fixture を引数で受けて注入させる
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = Path(repo["root"])
    document = log(
        run(
            [
                result("edit.py", 2, "first line\nsecond line", level="error"),
                result("edit.py", 999, "far away", level="error"),
                result("https://example.invalid/a.py", 1, "remote", level="error"),
            ],
            versionControlProvenance=[{"revisionId": repo["head"]}],
        )
    )
    path = write(root.parent, "mixed.sarif", document)
    review = create_range_review(
        root, "main..feature", "mine", DeclaredInputs(sarif=_request(root, path))
    )
    capsys.readouterr()
    assert main(["show", str(review.id)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "上げた判断の箇所: 1 件、差分の外: 1 件" in out
    assert "捨てた指摘: 1 件（unsupported_scheme 1）" in out
    assert "first line second line" in out
