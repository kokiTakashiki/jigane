"""副コマンドの実行の関数へ渡す、注入する依存。"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from jigane.llm_runs import claude


def stdio_is_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def read_answer(prompt: str) -> str:
    # 標準出力は --json の本体に空ける
    print(prompt, end="", file=sys.stderr, flush=True)
    try:
        return input()
    except EOFError:
        # 入力が閉じた。答え無しは断りと同じなので、呼ぶ側が y でない答えとして扱える空を返す。
        # 問いの行は改行されずに残るので、次の出力が同じ行に続かないよう改行する
        print(file=sys.stderr)
        return ""


@dataclass(frozen=True)
class Context:
    """入口が組み立て、解析した引数とともに実行の関数へ渡す依存。

    Attributes:
        invoke: Claude Code CLI を起動する関数。検査は偽物に差し替える。
        is_terminal: 標準入力と標準出力がともに端末かを返す関数。同意を取れるかの判定に使う。
        read_answer: 質問を出して利用者の答えを読む関数。
    """

    invoke: claude.Invoke
    # 呼び出しのたびにモジュールの関数を引く。検査が差し替えるのはこのモジュールの関数
    # なので、関数を直に渡すと差し替えが効かない
    is_terminal: Callable[[], bool] = field(
        default=lambda: stdio_is_terminal()  # noqa: PLW0108 差し替えが効くよう呼び出しのたびに引く
    )
    read_answer: Callable[[str], str] = field(
        default=lambda prompt: read_answer(prompt)  # noqa: PLW0108 差し替えが効くよう呼び出しのたびに引く
    )
