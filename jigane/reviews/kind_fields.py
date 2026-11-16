"""種類から導く JSON の項目の定義。種類（ReviewKind）以外に依存しない。"""

from typing import Any

from jigane.storage.store import ReviewKind


def kind_fields_of(kind: ReviewKind) -> dict[str, Any]:
    return {
        "kind": str(kind),
        "mode": kind.mode,
        "is_diff": kind.is_diff,
        "is_pull_request": kind.is_pull_request,
    }
