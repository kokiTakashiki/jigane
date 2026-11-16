from pathlib import Path

import pytest

from jigane.design.review_documents import read_document
from jigane.reviews.lifecycle import ReviewError


def test_read_document_refuses_symlink_to_outside(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.md").symlink_to(outside)
    with pytest.raises(ReviewError, match="シンボリックリンク"):
        read_document(root, "link.md", "設計書")
