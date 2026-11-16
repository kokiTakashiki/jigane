"""読めない記述の構文エラーの文面と述語。標準エラーの案内と警告の説明が同じ記録から組む。

構文エラー以外の文面は、言語アダプタが unsupported_syntax で返す。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class UnreadableWording:
    """一つの種類の文面。"""

    title: str
    fix_hint: str


# 読めない記述が及ぼす影響の述語。主語は案内が「これらの記述は」、警告が「この記述は」
PREDICATE = "一部の検査で読まれず、その対象から外れます"

SYNTAX_ERROR_WORDING = UnreadableWording(
    "構文エラー",
    "構文エラーを直してください。わざと壊したテスト用の入力なら、その旨を人が確かめてください。",
)
