"""開発の道具（python、uv、actionlint、zstd など）を起動する口。

起動してよい実行ファイルの一覧（process の git と gh）を通さない。
配布物に入らない scripts と tests は、それ以外の道具をここから起動する。
本体（jigane/）はこのファイルを import できない（pyproject.toml の import-linter の契約）。
起動そのものはここで行い、process からは起動を行わない部品（解決、環境、失敗の変換）だけを使う。
git の場所の変数の除去、Windows の解決、実行ファイルが無い失敗の変換
（returncode None、missing_executable）は run_command と同じ。
"""

import subprocess
from pathlib import Path

from jigane.git.process import CommandError, child_environment, launch_failure, resolve_command


def run_tool(
    args: list[str],
    cwd: Path | None = None,
    stdin: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    launched = resolve_command(args, env)
    try:
        completed = subprocess.run(
            launched,
            cwd=cwd,
            env=child_environment(args, env),
            input=stdin,
            check=True,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
    except subprocess.CalledProcessError as error:
        raise CommandError(
            args,
            error.stderr or "",
            stdout=error.stdout or "",
            returncode=error.returncode,
            cwd=cwd,
        ) from error
    except FileNotFoundError as error:
        failure = launch_failure(args, cwd, error)
        if failure is None:
            # 作業ディレクトリが無い失敗は、実行ファイルの失敗ではないので元の例外のまま渡す
            raise
        raise failure from error
    return completed.stdout
