"""起動ごとに入力を JSON へ記録し、役ごとのスキーマに合う最小の出力を返す偽の claude。

標準ライブラリだけで動く。引数は「記録の置き場、親の環境の JSON ファイル、以降は claude の引数」。
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# プロンプトの冒頭の「あなたは、…」の文に含まれる印と、役の名前
ROLES = [
    ("開示済みの限界の指摘を", "bundle_disclosed"),
    ("テーマへ束ねる役", "themes"),
    ("指摘を崩す立場の反証役", "rebuttal"),
    ("指摘と反証を受けて判定する判定役", "judge_rebuttal"),
    ("指摘、代案、擁護を受けて判定する判定役", "judge_alternative"),
    ("代案を示す代案役", "alternative"),
    ("現案を擁護する擁護役", "defense"),
    ("設計書の方針のレビューを", "macro_design"),
    ("方針のレビューを図だけで", "macro_diagram"),
    ("セキュリティをレビューする", "security"),
    ("変更の実装をレビューする", "micro"),
]
OVERRIDES: dict[str, Any] = {
    "verdict": "kept",
    "severity": "warning",
    "in_scope": True,
    "scope_reason": "",
    "path": os.environ.get("FAKE_PATH", "src/calc.py"),
    "start_line": 1,
    "end_line": 2,
    "verified": True,
}
FINDING_COUNT = 3
# スキーマから組む JSON の値は形が実行時に決まるので、静的な型は付けない
JsonValue = Any


def role_of(prompt: str) -> str:
    match = re.search(r"あなたは、([^。\n]*)", prompt)
    head = match.group(1) if match else prompt[:200]
    for marker, role in ROLES:
        if marker in head:
            return role
    return "unknown"


def resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        node = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def _choice(schema: dict[str, Any], root: dict[str, Any], key: str | None) -> JsonValue:
    if "enum" in schema:
        if key in OVERRIDES and OVERRIDES[key] in schema["enum"]:
            return OVERRIDES[key]
        return schema["enum"][0]
    if key in OVERRIDES:
        return OVERRIDES[key]
    if "const" in schema:
        return schema["const"]
    options = [resolve(option, root) for option in schema.get("anyOf", schema.get("oneOf", []))]
    options = [option for option in options if option.get("type") != "null"] or options
    return build(options[0], root, key)


def _scalar(kind: str | None, schema: dict[str, Any]) -> JsonValue:
    if kind == "string":
        return "x"
    if kind == "integer":
        return max(schema.get("minimum", 1), 1)
    if kind == "number":
        return 0.0
    if kind == "boolean":
        return True
    return None


def build(schema: dict[str, Any], root: dict[str, Any], key: str | None = None) -> JsonValue:
    schema = resolve(schema, root)
    if any(name in schema for name in ("enum", "const", "anyOf", "oneOf")) or key in OVERRIDES:
        return _choice(schema, root, key)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), kind[0])
    if kind == "object" or "properties" in schema:
        required = schema.get("required", [])
        return {
            name: build(sub, root, name)
            for name, sub in schema.get("properties", {}).items()
            if name in required
        }
    if kind == "array":
        count = max(schema.get("minItems", 0), 1 if key == "findings" else 0)
        return [build(schema.get("items", {}), root) for _ in range(count)]
    return _scalar(kind, schema)


def embedded_candidates(prompt: str) -> list[dict[str, Any]]:
    after = prompt.split("候補 (JSON):\n", 1)
    if len(after) < 2:
        return []
    candidates: list[dict[str, Any]] = json.loads(re.split(r"\n\n|\Z", after[1], maxsplit=1)[0])
    return candidates


def output_for(role: str, schema: dict[str, Any], prompt: str) -> dict[str, Any]:
    output = build(schema, schema)
    assert isinstance(output, dict)
    if role in ("micro", "security"):
        base = output["findings"][0]
        properties = schema["properties"]["findings"]["items"].get("properties", {})
        rows = []
        for number in range(FINDING_COUNT):
            row = {
                **base,
                "claim": f"claim {number}",
                "evidence": [{"path": OVERRIDES["path"], "start_line": 1, "end_line": 2}],
            }
            # 2 件目以降は開示済みの限界として挙げ、束ねの役を起こす
            if number >= 1 and "disclosed_at" in properties:
                row["disclosed_at"] = "README.md 限界の節"
            rows.append(row)
        output["findings"] = rows
    candidates = embedded_candidates(prompt)
    if "items" in output and candidates:
        item_schema = resolve(schema["properties"]["items"], schema)["items"]
        template = build(item_schema, schema)
        output["items"] = [{**template, "index": c["index"]} for c in candidates]
    return output


def main() -> int:
    records = Path(sys.argv[1])
    parent: dict[str, str] = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    argv = sys.argv[3:]
    prompt = sys.stdin.read()
    now = dict(os.environ)
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    role = role_of(prompt)
    mcp_config = None
    if "--mcp-config" in argv:
        mcp_config = Path(argv[argv.index("--mcp-config") + 1]).read_text(encoding="utf-8")
    output = output_for(role, schema, prompt)
    record = {
        "role": role,
        "argv": argv,
        "environment": {
            "added": {k: v for k, v in now.items() if k not in parent},
            "removed": sorted(k for k in parent if k not in now),
        },
        "cwd": str(Path.cwd()),
        "stdin": prompt,
        "mcp_config": mcp_config,
    }
    # 起動の順は時刻の並びで残す。並行の起動でも名前が重ならないよう、プロセス番号を添える
    name = f"{time.time_ns()}-{os.getpid()}-{role}.json"
    (records / name).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    usage = {"input_tokens": 10, "output_tokens": 5}
    usage |= {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    sys.stdout.write(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "fake",
                "structured_output": output,
                "num_turns": 1,
                "total_cost_usd": 0.01,
                "usage": usage,
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
