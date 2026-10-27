"""gates.json を試験で書く支え。番号の台帳の鍵は必須なので、書く関数を一つにする。"""

import json
from pathlib import Path
from typing import Any

from jigane.gates.entry import NumberLedger


def write_gates_file(directory: Path, state: dict[str, Any]) -> None:
    """state に台帳の鍵が無ければ空の台帳の鍵を補って gates.json に書く。"""
    (directory / "gates.json").write_text(
        json.dumps({**NumberLedger().to_json(), **state}), encoding="utf-8"
    )
