"""読めない記述の種類。警告の項目が型で持ち、gates.json にも書く。"""

import re
from dataclasses import dataclass
from typing import ClassVar

_LANGUAGE_NAME = r"[a-z][a-z0-9]*"
_KIND_NAME = r"[a-z][a-z0-9_]*"
_LANGUAGE_NAME_PATTERN = re.compile(_LANGUAGE_NAME)
_KIND_NAME_PATTERN = re.compile(_KIND_NAME)
_VALUE_PATTERN = re.compile(f"{_LANGUAGE_NAME}_{_KIND_NAME}")

_SYNTAX_ERROR_VALUE = "syntax_error"


@dataclass(frozen=True)
class ConstructKind:
    """読めない記述の種類。値は gates.json の kind の綴りで、言語名と種類の名前を `_` で繋ぐ。"""

    value: str

    # 値はクラスの定義の後で入れる（自分の型のインスタンスをクラス本体の中では作れない）
    SYNTAX_ERROR: ClassVar["ConstructKind"]

    def __post_init__(self) -> None:
        if self.value != _SYNTAX_ERROR_VALUE and _VALUE_PATTERN.fullmatch(self.value) is None:
            raise ValueError(f"読めない記述の種類の綴りが不正です: {self.value!r}")

    def __str__(self) -> str:
        return self.value

    @classmethod
    def of_language(cls, language_name: str, kind_name: str) -> "ConstructKind":
        """アダプタの言語名と種類の名前から値を組む。綴りの文法の正本はここ。"""
        if _LANGUAGE_NAME_PATTERN.fullmatch(language_name) is None:
            raise ValueError(
                f"言語名 {language_name!r} は {_LANGUAGE_NAME} に合いません"
                "（小文字と数字だけで、先頭は小文字）"
            )
        if _KIND_NAME_PATTERN.fullmatch(kind_name) is None:
            raise ValueError(f"種類の名前 {kind_name!r} は {_KIND_NAME} に合いません")
        value = f"{language_name}_{kind_name}"
        if value == _SYNTAX_ERROR_VALUE:
            raise ValueError(f"{value!r} は構文エラーの値と衝突します")
        return cls(value)

    @property
    def entry_id(self) -> str:
        """警告の id。kind から決まる。"""
        return "UNREADABLE-" + self.value.upper().replace("_", "-")


ConstructKind.SYNTAX_ERROR = ConstructKind(_SYNTAX_ERROR_VALUE)
