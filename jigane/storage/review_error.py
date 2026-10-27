"""レビューの読み書きで、利用者へそのまま伝える失敗。"""

from jigane.storage.store import ReviewId


class ReviewError(Exception):
    pass


class ReviewExistsError(ReviewError):
    """作ろうとしたレビューの識別子が、既にあるレビューと重なった。

    呼ぶ側は誤りとして止めず、`review_id` のレビューを開ける。
    """

    def __init__(self, review_id: ReviewId, message: str) -> None:
        super().__init__(message)
        self.review_id = review_id
