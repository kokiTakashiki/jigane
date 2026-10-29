"""SARIF の読み込みの試験が共有する、SARIF の組み立てと書き出し。"""

import json
from pathlib import Path
from typing import Any


def result(
    uri: str | None = "src/a.py",
    start_line: object = 3,
    message: object = "msg",
    **extra: object,
) -> dict[str, Any]:
    """位置を一つ持つ結果。uri が None なら位置を持たない結果にする。"""
    body: dict[str, Any] = {"message": {"text": message}, **extra}
    if uri is not None:
        region: dict[str, Any] = {"startLine": start_line} if start_line is not None else {}
        body["locations"] = [
            {"physicalLocation": {"artifactLocation": {"uri": uri}, "region": region}}
        ]
    return body


def run(
    results: list[dict[str, Any]], name: object = "ruff", version: object = "1.0", **extra: object
) -> dict[str, Any]:
    driver: dict[str, Any] = {"name": name}
    if version is not None:
        driver["version"] = version
    return {"tool": {"driver": driver}, "results": results, **extra}


def log(*runs: dict[str, Any], version: object = "2.1.0") -> dict[str, Any]:
    return {"version": version, "runs": list(runs)}


def write(directory: Path, name: str, document: object) -> Path:
    path = directory / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path
