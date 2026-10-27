"""作業ツリーが setup 済みかを、通信せずに安く確かめる。標準ライブラリだけで動く。

implementer の終わりのゲートは、setup をしていない作業ツリーでも走る。そのとき文法が無く
GrammarError で落ちると、ゲートの結果が作業ツリーの用意の有無で変わる。ゲートの最初でこれを
走らせ、用意が無いことを一行で伝えて止める。
ゲートの中では setup を走らせない（通信と重い導入を含むため）。

使い方: check_setup.py --env <根> [--env <根>...]
根は `.venv` を持つ uv のプロジェクトの根（環境が複数あるときは、根を並べて渡す）。
確かめるのは、根の `.venv` と、その環境が文法を置く場所のファイルの有無だけで、
sha256 は照合しない。
置き場の決め方は jigane.languages.grammars（cache_root、library_dir、library_path）に任せ、
規則を写さないため、その環境の python に問い合わせる。uv の環境を作らないよう、
`uv run --no-project python` で走らせる。
"""

import argparse
import subprocess
import sys
from pathlib import Path

# 環境の python で走らせる問い合わせ。文法の共有ライブラリが置き場に無い文法の名前を一行ずつ出す。
_PROBE = """\
from jigane.languages import grammars
root = grammars.cache_root()
for name in grammars.GRAMMARS:
    if grammars.library_path(root, name) is None:
        print(name)
"""
_GUIDANCE = "make setup を走らせてください（Windows は just setup）。"


def _venv_python(root: Path) -> Path:
    # venv の python の置き場は OS で違う（Windows は Scripts）。OS は判定せず、在る方を取る
    windows = root / ".venv" / "Scripts" / "python.exe"
    return windows if windows.is_file() else root / ".venv" / "bin" / "python"


def missing_in(root: Path, probe_source: str = _PROBE) -> str | None:
    """root の環境で足りないものを一句で返す。揃っていれば None。"""
    python = _venv_python(root)
    if not python.is_file():
        return f"{root / '.venv'} が無い"
    probe = subprocess.run(
        [str(python), "-I", "-c", probe_source], capture_output=True, text=True, check=False
    )
    if probe.returncode != 0:
        # 環境はあるが jigane を読めない。依存が入っていない状態として扱う
        lines = probe.stderr.strip().splitlines()
        cause = f"（{lines[-1].strip()}）" if lines else ""
        return f"{root / '.venv'} から jigane を読めない{cause}"
    names = probe.stdout.split()
    if not names:
        return None
    return f"{root / '.venv'} の文法 {'、'.join(names)} が置き場に無い"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="作業ツリーが setup 済みかを確かめる")
    parser.add_argument("--env", action="append", required=True, type=Path, metavar="ROOT")
    problems = [
        problem
        for root in parser.parse_args(argv).env
        if (problem := missing_in(root)) is not None
    ]
    if not problems:
        # 揃っている。ゲートの続きに進めるよう、何も出さない
        return 0
    print(f"作業ツリーが setup されていない: {'；'.join(problems)}。{_GUIDANCE}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
