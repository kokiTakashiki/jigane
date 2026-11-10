"""レビューの失敗の import の入口。開発用リポジトリの継ぎ目のため残す。定義は store.py。"""

from jigane.storage.store import ReviewError, ReviewExistsError

__all__ = ["ReviewError", "ReviewExistsError"]
