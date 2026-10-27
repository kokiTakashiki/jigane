#!/usr/bin/env python3
"""呼ばれたら引数と時刻を記録し、claude の失敗の形で即座に失敗する偽の claude。

JIGANE_CLAUDE_BINARY にこのファイルを向けると、確かめなどで起動の要求が
出ても、本物の claude（LLM）は決して始まらない。標準ライブラリだけで動く。

記録の置き場は環境変数 JIGANE_FAKE_CLAUDE_LOG（追記する JSON Lines のファイル）。
無ければ標準エラーに出す。標準入力は読まない。
"""

import json
import sys
from datetime import UTC, datetime
from os import environ
from pathlib import Path

LOG_ENVIRONMENT = "JIGANE_FAKE_CLAUDE_LOG"
EXIT_CODE = 1


def main(argv: list[str]) -> int:
    entry = json.dumps({"time": datetime.now(UTC).isoformat(), "argv": argv}, ensure_ascii=False)
    log_path = environ.get(LOG_ENVIRONMENT)
    if log_path:
        with Path(log_path).open("a", encoding="utf-8") as log:
            log.write(entry + "\n")
    else:
        sys.stderr.write(f"fake claude: {entry}\n")
    # claude の --output-format json の失敗と同じ形。理由は標準出力の subtype と result に書く
    sys.stdout.write(
        json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": "fake claude: 本物の claude は起動していない",
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return EXIT_CODE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
