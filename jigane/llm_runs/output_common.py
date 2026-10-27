"""LLM の出力のモデルが共有する約束。余計な項目を拒み、型の変換をしない。"""

from typing import Annotated

from pydantic import BeforeValidator, ConfigDict, NonNegativeInt

STRICT_OUTPUT = ConfigDict(extra="forbid", strict=True)


def _whole_float_to_int(value: object) -> object:
    # JSON スキーマの integer は 1.0 のような整数値の浮動小数も受け付ける。
    # strict のまま、それだけを int に直す。1.5、真偽値、数字の文字列は直さず、strict が拒む
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


# スキーマは integer のまま変わらない。BeforeValidator は検証だけに効く
SchemaInt = BeforeValidator(_whole_float_to_int)
NonNegativeSchemaInt = Annotated[NonNegativeInt, SchemaInt]
