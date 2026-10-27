"""保存物の素材を、形を残したまま縮めて tests/fixtures/store へ書き出す。

使い方: python scripts/shrink_store_fixtures.py <素材の根> <出力先>
配列は、鍵の集合と列挙の値が同じ行を一つにまとめる。手順の全体は tests/fixtures/store/README.md。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

_MAX_KEPT_ITEMS = 3
_EXPECTED_ARGV_LENGTH = 3  # プログラム名と二つの引数

# 値そのものが形の違いになる欄。配列の欄なら要素の値ごとに残す。
# ここに無い文字列と数値は「値の型」だけで形を比べる。
ENUM_KEYS = frozenset(
    {
        "kind", "level", "severity", "triage", "group", "viewpoint", "mode", "state",
        "change_kind", "declaration_kind", "visibility", "verdict", "role", "shape",
        "record_kind", "cost_source", "terminal_reason", "perspective", "confidence",
        "cause", "theme",
        "stale_groups", "finding_ids", "picked", "held",
    }
)  # fmt: skip

# キーが突き合わせの対象で、行ごとに値の形だけを見て間引く辞書の欄。
# line_counts は入れない。鍵が語彙の値（by_kind、by_level など）で、間引くと鍵が消える。
DICT_KEYS = frozenset({"keys", "regions"})


def signature(value: object, key: str = "") -> object:
    if isinstance(value, dict):
        return tuple(sorted((k, signature(v, k)) for k, v in value.items()))
    if isinstance(value, list):
        return ("list", tuple(sorted({repr(signature(v, key)) for v in value})))
    if key in ENUM_KEYS:
        return ("enum", repr(value))
    if isinstance(value, bool) or value is None:
        return repr(value)
    return type(value).__name__


def shrink(value: Any, key: str = "") -> Any:  # noqa: ANN401 素材の JSON の任意の値を再帰で受けて返す
    if isinstance(value, list):
        kept: dict[object, Any] = {}
        for item in value:
            reduced = shrink(item)
            kept.setdefault(signature(reduced, key), reduced)
        return list(kept.values())
    if isinstance(value, dict):
        reduced_items = {k: shrink(v, k) for k, v in value.items()}
        if key in DICT_KEYS and len(reduced_items) > _MAX_KEPT_ITEMS:
            kept_items: dict[object, tuple[str, Any]] = {}
            for k, v in reduced_items.items():
                kept_items.setdefault(signature(v, k), (k, v))
            return dict(kept_items.values())
        return reduced_items
    return value


def restore_enums(value: Any, key: str = "") -> Any:  # noqa: ANN401 素材の JSON の任意の値を再帰で受けて返す
    """素材の作成時に "text" へ潰れた列挙の欄を、読める値へ戻す。"""
    if isinstance(value, dict):
        restored = {k: restore_enums(v, k) for k, v in value.items()}
        if key == "tool_claim" and restored.get("basis") == "text":
            restored["basis"] = "docs" if restored.get("relies") else "none"
        return restored
    if isinstance(value, list):
        if key == "stale_groups":
            return ["macro" if item == "text" else item for item in value]
        return [restore_enums(item, key) for item in value]
    return value


def fix_identifier(text: str) -> str:
    """素材の識別子の区切りを、範囲は @、設計書は + に直す（素材の写しでは : を前置していた）。"""
    return text.replace("owner/repo:@", "owner/repo@").replace("owner/repo:+", "owner/repo+")


# 出力先として消してよい位置。手で書く文書は消さずに残す
OUTPUT_ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "store"
HAND_WRITTEN = frozenset({"README.md", "CATALOG.md"})


def check_destination(destination: Path) -> Path:
    resolved = destination.resolve()
    if resolved != OUTPUT_ROOT and OUTPUT_ROOT not in resolved.parents:
        raise SystemExit(f"出力先は {OUTPUT_ROOT} の下に限る: {destination}")
    return resolved


def main(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise SystemExit(f"素材の根が無い: {source}")
    destination = check_destination(destination)
    # 入力と出力が同じ置き場でも読み切ってから消せるよう、先に全部を作る
    outputs: dict[Path, str] = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.name in HAND_WRITTEN:
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            shrunk = shrink(restore_enums(json.loads(text)))
            text = json.dumps(shrunk, ensure_ascii=False, indent=1) + "\n"
        outputs[destination / path.relative_to(source)] = fix_identifier(text)
    if destination.exists():
        for entry in destination.iterdir():
            if entry.name in HAND_WRITTEN:
                continue
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
    for target, text in outputs.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != _EXPECTED_ARGV_LENGTH:
        raise SystemExit("使い方: python scripts/shrink_store_fixtures.py <素材の根> <出力先>")
    main(Path(sys.argv[1]), Path(sys.argv[2]))
