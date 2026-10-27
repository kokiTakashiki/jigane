import shutil
from pathlib import Path

import pytest
from pr_support import commit_all

from jigane.commands.subcommands.refresh import refresh_review
from jigane.design.review_documents import (
    create_design_doc_review,
    document_version,
    normalize_newlines,
    read_attachments,
    read_document,
)
from jigane.design.section_map import build_section_map
from jigane.groups.definitions import MICRO
from jigane.skills import sharded, shards

DOCUMENT = "docs/shop-design.md"
MEMO = "docs/reports/history-split.md"
DOCUMENT_BODY = "# 題\n\n## 1. 序論\n\n`Cart` を持つ。\n\n## 2. 結び\n\n以上。\n"
MEMO_BODY = "# 検証メモ\n\n分割で履歴が残る。\n"


def _repo(template: dict[str, str], tmp_path: Path, name: str, newline: bytes) -> Path:
    root = tmp_path / name
    shutil.copytree(template["root"], root, symlinks=True)
    (root / "cart.py").write_text("class Cart:\n    pass\n", encoding="utf-8")
    (root / "docs" / "reports").mkdir(parents=True, exist_ok=True)
    for path, body in ((DOCUMENT, DOCUMENT_BODY), (MEMO, MEMO_BODY)):
        (root / path).write_bytes(body.encode("utf-8").replace(b"\n", newline))
    commit_all(root, "add design")
    return root


def _inserts(root: Path) -> list[str]:
    review = create_design_doc_review(root, DOCUMENT, name=root.name, attach=[MEMO])
    plan = shards.build_plan(review, MICRO)
    assert plan.drifts == ()
    return [
        sharded._design_insert(shard)
        for shard in plan.shards
        if isinstance(shard, shards.DesignDocumentShard | shards.DesignFileShard)
    ]


@pytest.mark.parametrize("newline", [b"\r\n", b"\r"])
def test_shard_inserts_match_the_lf_files(
    repo_template: dict[str, str], tmp_path: Path, newline: bytes
) -> None:
    lf = _repo(repo_template, tmp_path, "lf", b"\n")
    other = _repo(repo_template, tmp_path, "other", newline)
    inserts = _inserts(other)
    assert inserts == _inserts(lf)
    assert "\r" not in "".join(inserts)


def test_attachment_content_is_lf_and_not_stale_for_crlf(
    repo_template: dict[str, str], tmp_path: Path
) -> None:
    root = _repo(repo_template, tmp_path, "crlf", b"\r\n")
    review = create_design_doc_review(root, DOCUMENT, attach=[MEMO])
    (content,) = read_attachments(review)
    assert content.text == MEMO_BODY
    assert not content.stale


def test_version_is_taken_from_the_raw_bytes() -> None:
    raw = b"a\r\nb\r\n"
    assert document_version(raw) != document_version(normalize_newlines(raw.decode()).encode())


def test_read_document_returns_raw_bytes_and_normalized_text(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_bytes(b"a\r\nb\rc\n")
    document = read_document(path)
    assert document.data == b"a\r\nb\rc\n"
    assert document.text == "a\nb\nc\n"
    assert document.version == document_version(b"a\r\nb\rc\n")


def test_section_hashes_do_not_depend_on_crlf() -> None:
    # 本文は行に分けて "\n" で繋ぎ直してからハッシュにするため、CRLF のままの本文でも同じ値になる。
    # 単独の CR は節の解析が行と見なさない。read_document が先に LF へそろえるので含めない
    lf = build_section_map(DOCUMENT, DOCUMENT_BODY)
    other = build_section_map(DOCUMENT, DOCUMENT_BODY.replace("\n", "\r\n"))
    assert [(r.key, r.content_hash, r.new_span) for r in other.regions] == [
        (r.key, r.content_hash, r.new_span) for r in lf.regions
    ]


def test_refresh_after_lf_to_crlf_reports_no_changed_sections(
    repo_template: dict[str, str], tmp_path: Path
) -> None:
    root = _repo(repo_template, tmp_path, "refresh", b"\n")
    review = create_design_doc_review(root, DOCUMENT)
    (root / DOCUMENT).write_bytes(DOCUMENT_BODY.encode().replace(b"\n", b"\r\n"))
    commit_all(root, "crlf")
    result = refresh_review(review.id)
    assert result.updated
    assert result.region_changes.changed == []
    assert result.region_changes.added == []
    assert result.region_changes.removed == []
