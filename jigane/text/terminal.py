import re

# ESC で始まる列（CSI、OSC、それ以外の ESC 二文字）を先に除き、残りの制御文字を除く
_ESCAPE_SEQUENCE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-Z\\-_]|\x1b"
)
# 文字の並びを入れ替える双方向制御も表示を偽るので除く
# （U+061C、U+200E-200F、U+202A-202E、U+2066-2069）
_CONTROL_CHARACTER = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]"
)


def sanitize_text(text: str) -> str:
    """外のコードやレビューの対象から来た文を端末へ出せる形にする。

    改行とタブ以外の制御文字、双方向制御、端末の制御の列を除く。
    """
    return _CONTROL_CHARACTER.sub("", _ESCAPE_SEQUENCE.sub("", text))


def one_line_text(text: str) -> str:
    """sanitize_text に加え、改行とタブを含む空白の連なりを空白一つに畳む。

    ファイル名やパスは字下げした一行の欄に出すので、改行で行を偽らせない。
    """
    return " ".join(sanitize_text(text).split())
