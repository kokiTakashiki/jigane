import json

from jigane.judgment_map.classification import FilePair
from jigane.reviews.review_formats import PAIRS_FILE, read_pairs
from jigane.storage.review_access import read_stored_text
from jigane.storage.review_error import ReviewError
from jigane.storage.store import CheckedReviewId


def load_pairs(review_id: CheckedReviewId) -> list[FilePair]:
    """差分のレビューの、変更したファイルの対。"""
    text = read_stored_text(review_id, PAIRS_FILE)
    if text is None:
        raise ReviewError(
            "ファイルの対が保存されていない古いレビューです。"
            f"refresh で作り直してください: {review_id}"
        )
    return read_pairs(json.loads(text))
