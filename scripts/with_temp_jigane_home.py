"""JIGANE_HOME を一時のディレクトリへ向けて、引数のコマンドを走らせる。標準ライブラリだけで動く。

試験が本物のデータの置き場を書き換えないよう、試験を走らせる入口がこれを通す。
"""

import os
import subprocess
import sys
import tempfile


def main(command: list[str]) -> int:
    if not command:
        sys.stderr.write("使い方: with_temp_jigane_home.py <コマンド> [引数...]\n")
        return 2
    with tempfile.TemporaryDirectory(prefix="jigane-home-") as home:
        env = {**os.environ, "JIGANE_HOME": home}
        return subprocess.run(command, env=env, check=False).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
