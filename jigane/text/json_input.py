"""外から来た JSON を、入れ子の深さを先に数えてから読む。

json.loads が深い入れ子で RecursionError を出す深さは、Python の版で変わる。3.14 は再帰の回数で
なく実際のスタックの残りで判断するため、同じ入力でも、スタックの大きいスレッドでは読めてしまう。
読む前に自分で数え、どの版と環境でも同じ深さで止める。
"""

import json
import re

# 文字列の字句と、文字列の外の括弧。文字列の中の括弧を数えないよう、字句ごと読み飛ばす。
# 字句の中では、逃がした文字（\" と \\ を含む）を 2 文字の組として読み、閉じの引用符と見誤らない。
# 繰り返しの中に繰り返しを置かず、閉じない字句は入力の終わりまでを字句とする。照合が失敗して
# 後の位置から読み直すことが無いので、どの入力でも長さに比例する時間で終わる。閉じない字句は
# JSON として誤りなので、続く json.loads が拒む
_STRING_OR_BRACKET_TEXT = re.compile(r'"[^"\\]*(?:\\(?:[\s\S]|\Z)[^"\\]*)*(?:"|\Z)|[\[\]{}]')
_OPENERS = frozenset({"[", "{"})
_CLOSERS = frozenset({"]", "}"})


# 人間が書く設定のファイル（mcp.json、languages.json など）の入れ子の上限。正しい形は数段で収まる
SETTINGS_NESTING_LIMIT = 200


class JsonTooDeepError(ValueError):
    """JSON の入れ子が上限を超えた。"""

    def __init__(self, limit: int) -> None:
        super().__init__(f"JSON の入れ子が深すぎます（上限 {limit} 段）")
        self.limit = limit


def settings_too_deep_message(path: object, error: JsonTooDeepError) -> str:
    """人間が書く設定のファイルの JSON が深すぎたときに、端末へ出す文。"""
    return (
        f"{path} の JSON の入れ子が深すぎます（上限 {error.limit} 段）。"
        f"{path} を直してから、もう一度実行してください。"
    )


def reject_deep_json(document: str, limit: int) -> None:
    """文字列の外の [ と { の深さが limit を超えたら JsonTooDeepError を投げる。"""
    pattern = _STRING_OR_BRACKET_TEXT.finditer(document)
    depth = 0
    for token in pattern:
        lexeme = token.group()
        if lexeme in _OPENERS:
            depth += 1
            if depth > limit:
                raise JsonTooDeepError(limit)
        elif lexeme in _CLOSERS:
            depth -= 1
        else:
            # 文字列の字句。中の括弧を数えないために読み飛ばすだけ
            pass


def loads_bounded(document: str | bytes, limit: int, *, strict: bool = True) -> object:
    """入れ子の深さを確かめてから json.loads で読む。深すぎれば JsonTooDeepError。

    bytes は json.loads と同じ判定（UTF-8、UTF-16、UTF-32）で文字列にしてから、数えて読む。
    数える側と読む側が同じ文字列を見るためである。構文と符号の誤りは json.loads と同じ
    ValueError（JSONDecodeError、UnicodeDecodeError）のまま上げる。
    strict は json.loads へそのまま渡す。
    """
    text = (
        document.decode(json.detect_encoding(document), "surrogatepass")
        if isinstance(document, bytes)
        else document
    )
    reject_deep_json(text, limit)
    try:
        return json.loads(text, strict=strict)
    except RecursionError as error:
        # 上限より浅くても、呼ぶ側のスタックが小さければ起きうる。同じ誤りとして返す
        raise JsonTooDeepError(limit) from error
