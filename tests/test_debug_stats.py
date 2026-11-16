import json
from pathlib import Path
from typing import Any

import pytest
from review_fakes import current_form_findings

from jigane import commands
from jigane.commands import debug_stats
from jigane.reviews.lifecycle import Review, create_feature_review


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


def _make_review(root: Path, name: str, count: int) -> Review:
    created = create_feature_review(root, ["src"], name)
    state = {"groups": {"micro": [_finding(n) for n in range(1, count + 1)]}}
    (created.id.directory() / "findings.json").write_text(
        json.dumps(current_form_findings(state)), encoding="utf-8"
    )
    return created


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[Review]:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path / "home"))
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n" + "x = 1\n" * 50, encoding="utf-8")
    monkeypatch.chdir(root)
    return [_make_review(root, name, index + 1) for index, name in enumerate("abcde")]


def _stats(capsys: pytest.CaptureFixture[str], *extra: str) -> dict[str, Any]:
    assert commands.main(["debug", "stats", "--json", *extra]) == 0
    body: dict[str, Any] = json.loads(capsys.readouterr().out)
    return body


def test_keys_and_types(store: list[Review], capsys: pytest.CaptureFixture[str]) -> None:
    body = _stats(capsys)
    assert list(body) == ["reviews", "next", "skipped", "limits"]
    assert body["next"] is None
    assert body["skipped"] == 0
    assert body["limits"] == {
        "default_page_size": debug_stats.DEFAULT_PAGE_SIZE,
        "max_page_size": debug_stats.MAX_PAGE_SIZE,
        "max_findings_file_bytes": debug_stats.MAX_FINDINGS_FILE_BYTES,
    }
    assert len(body["reviews"]) == len(store)
    for row in body["reviews"]:
        assert list(row) == ["id", "finding_counts", "findings"]
        assert row["finding_counts"]["micro"] == len(row["findings"]["micro"])
        for finding in row["findings"]["micro"]:
            assert list(finding) == ["id", "severity", "triage"]


def test_broken_record_is_skipped_and_rest_returned(
    store: list[Review], capsys: pytest.CaptureFixture[str]
) -> None:
    (store[0].id.directory() / "review.json").write_text("{broken", encoding="utf-8")
    body = _stats(capsys)
    assert body["skipped"] == 1
    assert len(body["reviews"]) == len(store) - 1


def test_broken_findings_file_is_skipped(
    store: list[Review], capsys: pytest.CaptureFixture[str]
) -> None:
    (store[1].id.directory() / "findings.json").write_text("{broken", encoding="utf-8")
    body = _stats(capsys)
    assert body["skipped"] == 1
    assert len(body["reviews"]) == len(store) - 1


def test_page_size_and_after_walk_every_review_once(
    store: list[Review], capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str] = []
    after: str | None = None
    for _ in range(10):
        extra = ["--page-size", "2"] + ([] if after is None else ["--after", after])
        body = _stats(capsys, *extra)
        assert len(body["reviews"]) <= 2
        seen += [row["id"] for row in body["reviews"]]
        after = body["next"]
        if after is None:
            break
    assert after is None
    assert sorted(seen) == sorted(str(review.id) for review in store)


def test_record_over_size_limit_is_skipped(
    store: list[Review], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sizes = [(r.id.directory() / "findings.json").stat().st_size for r in store]
    limit = sorted(sizes)[2]
    monkeypatch.setattr(debug_stats, "MAX_FINDINGS_FILE_BYTES", limit)
    expected_skipped = sum(1 for size in sizes if size > limit)
    assert 0 < expected_skipped < len(sizes)
    body = _stats(capsys)
    assert body["skipped"] == expected_skipped
    listed = [row["id"] for row in body["reviews"]]
    assert len(listed) == len(sizes) - expected_skipped


def test_page_size_over_limit_is_rejected(
    store: list[Review], capsys: pytest.CaptureFixture[str]
) -> None:
    del store
    with pytest.raises(SystemExit):
        commands.main(["debug", "stats", "--page-size", str(debug_stats.MAX_PAGE_SIZE + 1)])
    capsys.readouterr()


def test_body_only_with_flag(store: list[Review], capsys: pytest.CaptureFixture[str]) -> None:
    del store
    plain = _stats(capsys)["reviews"]
    assert all("claim" not in f for row in plain for f in row["findings"]["micro"])
    with_body = _stats(capsys, "--with-body")["reviews"]
    claims = [f["claim"] for row in with_body for f in row["findings"]["micro"]]
    assert claims
    assert all(claim.startswith("claim") for claim in claims)


def test_skipped_sum_over_pages_equals_total(
    store: list[Review], capsys: pytest.CaptureFixture[str]
) -> None:
    (store[0].id.directory() / "review.json").write_text("{broken", encoding="utf-8")
    ids = sorted(str(r.id) for r in store[1:])
    for review in store[1:3]:
        (review.id.directory() / "findings.json").write_text("{broken", encoding="utf-8")
    total = 0
    seen: list[str] = []
    after: str | None = None
    while True:
        extra = ["--page-size", "1"] + ([] if after is None else ["--after", after])
        body = _stats(capsys, *extra)
        total += body["skipped"]
        seen += [row["id"] for row in body["reviews"]]
        after = body["next"]
        if after is None:
            break
    assert total == 3
    assert seen == [i for i in ids if i not in {str(r.id) for r in store[1:3]}]


def test_older_format_findings_file_is_skipped(
    store: list[Review], capsys: pytest.CaptureFixture[str]
) -> None:
    path = store[0].id.directory() / "findings.json"
    path.write_text(json.dumps({"format": 0, "groups": {}}), encoding="utf-8")
    body = _stats(capsys)
    assert body["skipped"] == 1
    assert len(body["reviews"]) == len(store) - 1


def test_after_unknown_mark_and_last_mark(
    store: list[Review], capsys: pytest.CaptureFixture[str]
) -> None:
    ids = sorted(str(r.id) for r in store)
    last = _stats(capsys, "--after", ids[-1])
    assert last["reviews"] == []
    assert last["next"] is None
    unknown = _stats(capsys, "--after", ids[1] + "-unknown")
    assert [row["id"] for row in unknown["reviews"]] == ids[2:]
    assert unknown["next"] is None


def test_text_output(store: list[Review], capsys: pytest.CaptureFixture[str]) -> None:
    assert commands.main(["debug", "stats"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert sorted(line.split("\t")[0] for line in lines[: len(store)]) == sorted(
        str(r.id) for r in store
    )
    assert "skipped\t0" in lines
    assert "暫定" in lines[-1]
