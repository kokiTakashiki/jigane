"""一覧が除いたレビューの警告を、ログの記録から取り出す補助。"""

import logging

import pytest

LISTING_LOGGER = "jigane.reviews.catalog"


def listing_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """一覧のロガーが WARNING で出した文を、出た順に返す。"""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LISTING_LOGGER and record.levelno == logging.WARNING
    ]
