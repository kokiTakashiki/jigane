"""外部コマンドの起動の窓口。"""

import errno
import ntpath
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path, PureWindowsPath
from typing import Any

from jigane.git.environment import GIT_LOCATION_VARIABLES


class CommandError(Exception):
    def __init__(
        self,
        args_run: list[str],
        stderr: str,
        *,
        stdout: str = "",
        returncode: int | None = None,
        missing_executable: bool = False,
        cwd: Path | None = None,
    ) -> None:
        where = f" (作業ディレクトリ: {cwd})" if cwd is not None else ""
        super().__init__(f"コマンドが失敗しました: {' '.join(args_run)}{where}\n{stderr}")
        self.command = args_run
        self.cwd = cwd
        self.stderr = stderr
        # 子の標準出力と終了コード。終了コード 1 と 3 は結果なので、呼ぶ側が標準出力を読む
        self.stdout = stdout
        self.returncode = returncode
        # 実行ファイルが PATH に無く、起動できなかった失敗
        self.missing_executable = missing_executable


CLAUDE_BINARY_ENVIRONMENT = "JIGANE_CLAUDE_BINARY"


def _executable_name(args: list[str]) -> str:
    # Windows 形式で読めば、POSIX でも "\\" 区切りのパスの名前を取り出せる
    return PureWindowsPath(args[0]).stem.lower()


def _is_claude(args: list[str]) -> bool:
    """先頭が claude の実行ファイルか。大文字小文字とパスの有無、拡張子を問わない。

    JIGANE_CLAUDE_BINARY で差し替えた実行ファイル（偽の claude など）も claude として数える。
    差し替えた名前が claude でなくても、llm_runs の外から起動できる抜け道にしないため。
    """
    if not args:
        return False
    names = {"claude"}
    replaced = os.environ.get(CLAUDE_BINARY_ENVIRONMENT)
    if replaced:
        names.add(_executable_name([replaced]))
    return _executable_name(args) in names


# 起動してよい実行ファイルの名前。名前だけを見る claude の拒否は、env や sh -c などの中継を
# 通すと抜けられるため、一覧の外の名前は起動の前に拒む。claude は run_claude_resolved だけが通る
LAUNCHABLE_EXECUTABLES = frozenset({"git", "gh"})


def _refuse_unlisted(args: list[str]) -> None:
    if not args:
        # 空の argv は subprocess が起動の失敗として扱う。名前が無いので一覧に当てられない
        return
    if _is_claude(args):
        raise CommandError(
            args,
            "claude は jigane.llm_runs から起動する。同意、予算、--restricted、"
            "秘密のファイルの拒否は、その口だけが付ける",
        )
    name = _executable_name(args)
    if name not in LAUNCHABLE_EXECUTABLES:
        allowed = "、".join(sorted(LAUNCHABLE_EXECUTABLES))
        raise CommandError(
            args,
            f"{name} は起動してよい実行ファイルの一覧（{allowed}）に無い。"
            "sh、env、cmd などの中継で別の実行ファイルを起動することも認めない",
        )


def child_environment(args: list[str], env: dict[str, str] | None) -> dict[str, str] | None:
    """git の場所の変数を除いた環境を返す。何も起動しない。"""
    # git 以外には手を付けない。他のコマンドが孫として起こす git へ変数を渡す意図まで消さないため
    if not args or Path(args[0]).name not in ("git", "git.exe"):
        return env
    base = os.environ if env is None else env
    return {k: v for k, v in base.items() if k not in GIT_LOCATION_VARIABLES}


# cmd.exe が構文として読む文字。.cmd と .bat は cmd.exe を通って起動され、引数の引用符の
# 扱いが Python の組み立てと食い違うため、これらを含む引数は安全に渡せない（BatBadBut）
CMD_SPECIAL_CHARACTERS = frozenset('&|<>^%!"\r\n')
CMD_SCRIPT_SUFFIXES = (".cmd", ".bat")
CLAUDE_NATIVE_INSTALL_URL = "https://code.claude.com/docs/en/setup#install-on-native-windows"


def launch_failure(
    args: list[str], cwd: Path | None, error: FileNotFoundError
) -> CommandError | None:
    """起動時の FileNotFoundError を、コマンドの名前を挙げた CommandError に変える。

    None は変換しないという意味で、呼び出し側が元の例外を投げ直す。
    """
    if not args:
        return None
    if error.filename == args[0]:
        return CommandError(
            args,
            f"実行ファイルが見つかりません: {args[0]}",
            missing_executable=True,
            cwd=cwd,
        )
    if error.filename is None and (cwd is None or cwd.is_dir()):
        # Windows の CreateProcess の WinError 2 は filename を持たない。作業ディレクトリが
        # 在るのに起動できないのは、コマンド側（.cmd の先の実行ファイルなど）の失敗
        return CommandError(args, f"コマンドを起動できませんでした: {args[0]} ({error})", cwd=cwd)
    # 作業ディレクトリが無い場合も FileNotFoundError になるので、実行ファイルでなければ変換しない
    return None


def _find_on_windows(name: str, path: str) -> str | None:
    if "\\" in name or "/" in name:
        # 利用者がディレクトリを書いた名前は、その場所だけを which に探させる
        return shutil.which(name)
    # shutil.which は Windows で path の前に現在のディレクトリを足して探す。jigane は
    # レビュー対象の木の中で走りうるので、絶対パスの項目だけを自分で順に引く
    for directory in path.split(";"):
        if not ntpath.isabs(directory):
            continue
        found = shutil.which(ntpath.join(directory, name))
        if found is not None:
            return found
    return None


def resolve_command(args: list[str], env: dict[str, str] | None) -> list[str]:
    """Windows では先頭を shutil.which で絶対パスに解く。それ以外は args をそのまま返す。

    CreateProcess が拡張子の無い名前に補うのは .exe だけなので、npm が置く claude.cmd などは
    PATHEXT を見る which で解かないと起動できない。POSIX の exec は PATH を自分で引く。
    """
    if not args or not is_windows():
        return args
    path = (os.environ if env is None else env).get("PATH")
    if path is None:
        path = os.environ.get("PATH", "")
    found = _find_on_windows(args[0], path)
    if found is None:
        raise CommandError(
            args,
            f"実行ファイルが見つかりません: {args[0]}",
            missing_executable=True,
        )
    if found.lower().endswith(CMD_SCRIPT_SUFFIXES):
        unsafe = [a for a in [found, *args[1:]] if CMD_SPECIAL_CHARACTERS & set(a)]
        if unsafe:
            if Path(args[0]).stem.lower() == "claude":
                remedy = (
                    "Claude Code の公式のインストーラで claude.exe を入れ直してください"
                    f"（手順: {CLAUDE_NATIVE_INSTALL_URL}）"
                )
            else:
                remedy = ".exe の実行ファイルを PATH の先に置いてください"
            raise CommandError(
                args,
                f"{found} は cmd.exe を通って起動されるため、cmd.exe の特別な文字"
                f'（& | < > ^ % ! " と改行）を含む JSON などの引数を安全に渡せません。{remedy}',
            )
    return [found, *args[1:]]


def run_resolved(
    args: list[str],
    **kwargs: Any,  # noqa: ANN401 subprocess.run の引数を、呼び出し側が選んだまま渡す
) -> subprocess.CompletedProcess[str]:
    """subprocess.run に、resolve_command と launch_failure の変換を足したもの。

    LAUNCHABLE_EXECUTABLES に無い名前（claude を含む）は断る。
    """
    _refuse_unlisted(args)
    return _run_resolved(args, **kwargs)


# blockReadsOutsideWorkingDirectories（jigane.llm_runs.claude の build_command）が効く最も古い版
MINIMUM_CLAUDE_VERSION = (2, 1, 257)
_CLAUDE_VERSION = re.compile(r"\s*(\d+)\.(\d+)\.(\d+)")
_CLAUDE_VERSION_TIMEOUT_SECONDS = 60
# 版を確かめ終えた claude の絶対パス。確かめは実体ごとにプロセスで一度だけ行う
_checked_claude_binaries: set[str] = set()
_checked_claude_lock = threading.Lock()


def run_claude_resolved(
    args: list[str],
    **kwargs: Any,  # noqa: ANN401 subprocess.run の引数を、呼び出し側が選んだまま渡す
) -> subprocess.CompletedProcess[str]:
    """run_resolved の claude 用。jigane.llm_runs.claude.run だけが呼ぶ。

    claude の実体を一度だけ解き、その実体の版を確かめてから、同じ実体で起動する。
    外からの呼び出しは ast-grep の規則が落とす。
    """
    launched = _resolve_claude(args, kwargs.get("env"))
    with _checked_claude_lock:
        if launched[0] not in _checked_claude_binaries:
            _require_claude_version(launched[0], kwargs)
            _checked_claude_binaries.add(launched[0])
    return _run_resolved(launched, **kwargs)


def _resolve_claude(args: list[str], env: dict[str, str] | None) -> list[str]:
    """先頭を claude の実体の絶対パスにした argv。版の確かめと起動に同じ実体を使うため。"""
    if is_windows():
        # 引数ごと解く。cmd.exe を通る実体の断りは引数を見るため、版の確かめより先に効かせる
        return resolve_command(args, env)
    path = (os.environ if env is None else env).get("PATH", os.environ.get("PATH", ""))
    found = shutil.which(args[0], path=path)
    if found is None:
        missing = f"実行ファイルが見つかりません: {args[0]}"
        raise CommandError(args, missing, missing_executable=True)
    # リンクの先ではなく、PATH で見つけた名前のまま起動する
    return [str(Path(found).absolute()), *args[1:]]


def _require_claude_version(binary: str, kwargs: dict[str, Any]) -> None:
    """古い、または版を読み取れない claude を起動させない。版の文字列は claude の自己申告。"""
    command = [binary, "--version"]
    try:
        completed = _run_resolved(
            command,
            cwd=kwargs.get("cwd"),
            env=kwargs.get("env"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_CLAUDE_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        completed = None
    match = None if completed is None else _CLAUDE_VERSION.match(completed.stdout or "")
    if completed is None or completed.returncode != 0 or match is None:
        shown = "応答がありません" if completed is None else (completed.stdout or completed.stderr)
        raise CommandError(
            command,
            f"Claude Code の版を読み取れないため起動しません（{shown.strip()[-200:]}）。"
            "`claude --version` が版を返すことを確かめ、2.1.257 以降を入れてください",
        )
    version = tuple(int(part) for part in match.groups())
    if version < MINIMUM_CLAUDE_VERSION:
        shown = ".".join(str(part) for part in version)
        raise CommandError(
            command,
            f"Claude Code {shown} は作業ディレクトリの外の読み取りを拒む設定を無視するため、"
            "起動しません。2.1.257 以降へ上げてください",
        )


def _run_resolved(
    args: list[str],
    **kwargs: Any,  # noqa: ANN401 subprocess.run の引数を、呼び出し側が選んだまま渡す
) -> subprocess.CompletedProcess[str]:
    launched = resolve_command(args, kwargs.get("env"))
    try:
        return subprocess.run(launched, **kwargs)  # noqa: PLW1510 check は呼び出し側が渡す
    except FileNotFoundError as error:
        failure = launch_failure(args, kwargs.get("cwd"), error)
        if failure is None:
            raise
        raise failure from error
    except NotADirectoryError as error:
        # Windows の CreateProcess は、作業ディレクトリが無いと WinError 267 の
        # NotADirectoryError を出す。POSIX の FileNotFoundError と同じ失敗として揃える
        cwd = kwargs.get("cwd")
        if cwd is None or Path(cwd).is_dir():
            raise
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(cwd)) from error


def run_command(
    args: list[str],
    cwd: Path | None = None,
    stdin: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    # 検査で差し替える唯一の入口。外部コマンドはすべてここを通す
    try:
        completed = run_resolved(
            args,
            cwd=cwd,
            input=stdin,
            env=child_environment(args, env),
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
    return completed.stdout


def is_windows() -> bool:
    return sys.platform == "win32"


# 確認の文の終わり。jigane/commands/context.py の read_answer が標準エラーへ出す問いの末尾と揃える
CONSENT_PROMPT_END = "（y/N） "
