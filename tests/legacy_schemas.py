"""段 4 で消した、旧の JSON スキーマ。

tests/fixtures/legacy_schemas の JSON のうち、finding.schema.json は本体が配っていた旧の共有の
スキーマ（jigane/schemas/）を移したもの。それ以外は、組み立てを消す直前の main（39ff6c1）で、
旧の関数
（macro.legacy_conclude_schema、micro.legacy_discovery_schema、security.legacy_discovery_schema、
adversarial.legacy_role_schemas）が返した値を、キーを整列して書き出したもの。
モデル由来のスキーマが、旧と同じ入力を受け付けることを比べるための相手として読む。
"""

import json
from pathlib import Path
from typing import Any

from jigane.findings.discard import DiscardKind
from jigane.findings.records import ToolBasis, Triage, Verdict
from jigane.groups.definitions import GroupId

_DIRECTORY = Path(__file__).parent / "fixtures" / "legacy_schemas"


def frozen_schema(name: str) -> dict[str, Any]:
    schema: dict[str, Any] = json.loads((_DIRECTORY / f"{name}.json").read_text(encoding="utf-8"))
    return schema


def load_finding_schema() -> dict[str, Any]:
    schema = frozen_schema("finding.schema")
    # 列挙が持つ値の集合は JSON に重ねて書くと食い違うため、読み込みで差し込む。
    properties = schema["properties"]
    properties["group"]["enum"] = [m.value for m in GroupId]
    properties["nit_reason"]["properties"]["kind"]["enum"] = [m.value for m in DiscardKind]
    # triage は null も取れるので、列挙を持つのは oneOf の最初の枝だけ。
    properties["triage"]["oneOf"][0]["enum"] = [m.value for m in Triage]
    definitions = schema["$defs"]
    definitions["tool_claim"]["properties"]["basis"]["enum"] = [m.value for m in ToolBasis]
    definitions["verdict"]["enum"] = [m.value for m in Verdict]
    return schema
