"""読めない記述の見出しと述語の文面。標準エラーの案内と警告の説明が同じ定数から組む。"""

from jigane.gates.entry import GateEntry, GateSeverity, UnreadableDetail
from jigane.gates.explanation import GateExplanation
from jigane.judgment_map.unreadable_constructs import ConstructKind, Occurrence

# 種類 -> 見出し。案内に出す順に並べる
TITLES = {
    ConstructKind.SYNTAX_ERROR: "構文エラー",
    ConstructKind.SWIFT_OPERATOR_FUNCTION: "名前を取れない演算子関数（Swift）",
    ConstructKind.CSHARP_NAMELESS_DECLARATOR: (
        "名前を取れない宣言子（C#。クラス直下の `Foo.Bar(x);` など）"
    ),
}
# 読めない記述が及ぼす影響の述語。主語は案内が「これらの記述は」、警告が「この記述は」
PREDICATE = "一部の検査で読まれず、その対象から外れます"
# 種類 -> 直し方
FIX_HINTS = {
    ConstructKind.SYNTAX_ERROR: (
        "構文エラーを直してください。わざと壊したテスト用の入力なら、その旨を人が確かめてください。"
    ),
    ConstructKind.SWIFT_OPERATOR_FUNCTION: (
        "演算子関数の中身を名前のある関数に移し、演算子からその関数を呼んでください。"
    ),
    ConstructKind.CSHARP_NAMELESS_DECLARATOR: (
        "文をメソッドかコンストラクタの中に移してください（C# ではクラス直下に文を書けません）。"
    ),
}

# 種類 -> 警告の id
ENTRY_IDS = {
    ConstructKind.SYNTAX_ERROR: "UNREADABLE-SYNTAX-ERROR",
    ConstructKind.SWIFT_OPERATOR_FUNCTION: "UNREADABLE-SWIFT-OPERATOR-FUNCTION",
    ConstructKind.CSHARP_NAMELESS_DECLARATOR: "UNREADABLE-CSHARP-NAMELESS-DECLARATOR",
}


def unreadable_entry(number: str, item: Occurrence) -> GateEntry:
    """一件の読めない記述から警告の GateEntry を組む。文面は案内と同じ定数から引く。"""
    explanation = GateExplanation(
        "jigane は、言語仕様の上で書ける記述でも、読めない記述をサポートしません。",
        f"{TITLES[item.kind]}です。この記述は{PREDICATE}。",
        FIX_HINTS[item.kind],
    )
    return GateEntry(
        number,
        ENTRY_IDS[item.kind],
        item.path,
        item.line,
        item.excerpt,
        explanation.to_json(),
        GateSeverity.UNREADABLE,
        UnreadableDetail(item.side),
    )
