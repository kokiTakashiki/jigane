"""GitHub Actions のワークフローを静的に検査し、gh の行を実物の gh で確かめる。make lint から走る。

  check_workflows.py [DIR]   DIR（既定は .github/workflows）の *.yml と、
                             DIR の親が .github のときその配下の action.yml / action.yaml
                             （composite action）を検査する

actionlint は文法と式、zizmor は安全を見る。zizmor はネットワークを使わず（--offline）、
pedantic の水準で見る。actionlint はワークフローだけを読み、action.yml は読めないので渡さない。
zizmor にはワークフローと composite action の両方を渡す。
gh の行は、run の塊から gh の呼び出しを取り出し、変数を仮の値に替えて実物の gh で走らせる。
偽のトークンを渡し、通信は行き止まりのプロキシへ向けるので、ネットワークにも認証にも触れない。
引数を通った呼び出しは、通信の段で proxyconnect の誤りになる。そこへ着かずに Usage を出したら、
引数の誤りとして落とす。--help に書かれない引数の組み合わせの禁止も、こうして拾う。
固定した gh の版は、release.yml の中で実際に走るランナーの gh の版とずれうる。
actionlint と gh は版と sha256 を固定した配布物を、初回だけ GitHub のリリースから取得して .tools/
に置く（照合に失敗したら使わずに落とす）。実体を用意できないときの終了コードは 4 で、指摘と区別
する。公開前の ci.yml は `on:` をコメントアウトしており、
そのままでは zizmor が入力として読めない。一時の置き場へ写しを作り、コメントアウトした `on:`
の塊だけを戻して検査する。元のファイルは変えない。指摘があればその終了コードで終わる。
"""

import argparse
import hashlib
import io
import os
import platform
import re
import shlex
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

from dev_launch import run_tool
from ruamel.yaml import YAML

from jigane.git.process import CommandError

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIRECTORY = ROOT / ".tools"
ZIZMOR_CONFIG = ROOT / ".github" / "zizmor.yml"
ACTIONLINT_VERSION = "1.7.12"
ACTIONLINT_URL = (
    "https://github.com/rhysd/actionlint/releases/download/"
    "v{version}/actionlint_{version}_{platform}"
)
# 配布物の sha256。リリースの actionlint_1.7.12_checksums.txt の値を直に写した
ACTIONLINT_SHA256 = {
    "darwin_amd64.tar.gz": "5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644",
    "darwin_arm64.tar.gz": "aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f",
    "linux_amd64.tar.gz": "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
    "linux_arm64.tar.gz": "325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6",
    "windows_amd64.zip": "6e7241b51e6817ea6a047693d8e6fed13b31819c9a0dd6c5a726e1592d22f6e9",
    "windows_arm64.zip": "cadcf7ea4efe3a68728893813643cebe1185e5b1d4be5b96245f65c9a4d5ea41",
}
GH_VERSION = "2.102.0"
GH_URL = "https://github.com/cli/cli/releases/download/v{version}/gh_{version}_{platform}"
# 配布物の sha256。リリースの gh_2.102.0_checksums.txt の値を直に写した
GH_SHA256 = {
    "macOS_amd64.zip": "b245f24eb2bf5f75b426b4c26da3651a107f8d5b6f4fddfbfccc5679041378b3",
    "macOS_arm64.zip": "da922c20d1792e5b2cbf375593d7a658acf034c12c84e007e71c76ef959c337e",
    "linux_amd64.tar.gz": "bb766f710eef8ede859c18578c72c327597cd4c8a85b06001b1f3843c6019386",
    "linux_arm64.tar.gz": "7862c86c72f43df3a2d93ddde6f473285b4e2af61b494849846827e513ef6484",
    "windows_amd64.zip": "ae64e556ecc240b200f7eba60d550e4bb60d78e860e69dd88c449405b86067f4",
    "windows_arm64.zip": "5dcf12aa8525eabd0c46ec414f323ab6cf65229fc2cd46543cc705001bbaf223",
}
# 誰も待ち受けない番。gh の通信はここで必ず断られる
DEAD_PROXY = "http://127.0.0.1:9"
# gh の通信の段に着いたことを示す誤りの文と、引数の誤りで止まったときに出る文
NETWORK_STAGE_MARKER = "proxyconnect"
USAGE_MARKER = "Usage:"
# run の中の変数の仮の値。名前の無い変数と、gh 以外のコマンドの置き換えは PLACEHOLDER にする
PLACEHOLDERS = {"REPOSITORY": "nobody/none", "TAG": "v0.0.0"}
PLACEHOLDER = "x0"
# シェルがコマンドの区切りとして読む記号。gh の呼び出しはここで終わる
COMMAND_SEPARATORS = frozenset({"(", ")", ";", "|", "||", "&", "&&", "<", ">", "<<<", ">>"})
# actionlint 自身は 0〜3 で終わる。4 は道具の実体を用意できなかったことを指し、指摘とは区別する
TOOL_UNAVAILABLE_EXIT_CODE = 4
DEFAULT_DIRECTORY = Path(".github/workflows")


def actionlint_platform() -> str:
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    machine = {"x86_64": "amd64", "AMD64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine()
    )
    if system is None or machine is None:
        message = f"actionlint の配布物が無い環境: {platform.system()} {platform.machine()}"
        raise RuntimeError(message)
    suffix = "zip" if system == "windows" else "tar.gz"
    return f"{system}_{machine}.{suffix}"


class ToolUnavailableError(RuntimeError):
    """検査の道具の実体を用意できなかった。ワークフローの指摘ではない。"""


class ActionlintUnavailableError(ToolUnavailableError):
    """actionlint の実体を用意できなかった。"""


class GhUnavailableError(ToolUnavailableError):
    """gh の実体を用意できなかった。"""


def download(url: str) -> bytes:
    # 固定した https の URL だけを開く
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 取得先は呼び出し側が渡す固定の https の URL
        return bytes(response.read())  # urlopen の型は Any のため、バイト列へ型を絞る


def extract_binary(data: bytes, name: str, executable: str) -> bytes:
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return archive.read(executable)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        member = archive.extractfile(executable)
        if member is None:
            message = f"配布物に {executable} が無い"
            raise ToolUnavailableError(message)
        return member.read()


def sidecar_of(target: Path) -> Path:
    return target.with_name(target.name + ".sha256")


def replace_atomically(path: Path, content: bytes, mode: int) -> None:
    """同じ置き場の一時ファイルへ書いてから置き換える。途中で落ちても path は壊れない。"""
    temporary = path.with_name(path.name + ".partial")
    temporary.write_bytes(content)
    temporary.chmod(mode)
    temporary.replace(path)


def installed_binary_is_intact(target: Path) -> bool:
    """置いてある実体が、置いたときに記録した sha256 と一致するか。"""
    sidecar = sidecar_of(target)
    if not (target.is_file() and sidecar.is_file()):
        return False
    recorded = sidecar.read_text(encoding="utf-8").strip()
    return hashlib.sha256(target.read_bytes()).hexdigest() == recorded


def fetch_actionlint(tools: Path = TOOLS_DIRECTORY) -> Path:
    """固定した版の actionlint の実体を返す。

    置いてある実体は、置いたときに記録した sha256 と照らして使う。無い、または一致しなければ
    取得し直す。配布物の sha256 が固定値と違えば置かずに落とす。
    """
    name = actionlint_platform()
    executable = "actionlint.exe" if name.startswith("windows") else "actionlint"
    target = tools / f"actionlint-{ACTIONLINT_VERSION}-{name.split('.')[0]}" / executable
    if installed_binary_is_intact(target):
        return target
    url = ACTIONLINT_URL.format(version=ACTIONLINT_VERSION, platform=name)
    data = download(url)
    digest = hashlib.sha256(data).hexdigest()
    if digest != ACTIONLINT_SHA256[name]:
        message = f"actionlint の sha256 が固定値と異なる: {digest}（{url}）"
        raise ActionlintUnavailableError(message)
    binary = extract_binary(data, name, executable)
    target.parent.mkdir(parents=True, exist_ok=True)
    # 実体を先に、記録を後に置く。間で落ちれば記録が無く、次は取り直しになる
    replace_atomically(target, binary, 0o755)
    replace_atomically(
        sidecar_of(target), hashlib.sha256(binary).hexdigest().encode("utf-8"), 0o644
    )
    return target


def run_actionlint(directory: Path) -> int:
    try:
        executable = fetch_actionlint()
    except (ToolUnavailableError, OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        sys.stderr.write(f"actionlint を用意できない: {error}\n")
        return TOOL_UNAVAILABLE_EXIT_CODE
    # actionlint は action.yml を読めないため、ワークフローだけを渡す
    workflows = sorted((directory / "workflows").glob("*.yml"))
    try:
        run_tool([str(executable), "-no-color", *map(str, workflows)])
    except CommandError as error:
        sys.stdout.write(error.stdout)
        sys.stderr.write(error.stderr)
        return error.returncode or 1
    return 0


def run_zizmor(directory: Path) -> int:
    # 設定は公開の写しに含まれない。無いときは設定なしで走らせる
    config = ["--config", str(ZIZMOR_CONFIG)] if ZIZMOR_CONFIG.is_file() else []
    try:
        run_tool(
            [
                "zizmor",
                "--offline",
                "--no-progress",
                "--pedantic",
                # CI など環境変数で出力が色付きに変わるのを避け、どこでも同じ文面にする
                "--color",
                "never",
                *config,
                str(directory),
            ]
        )
    except CommandError as error:
        sys.stdout.write(error.stdout)
        sys.stderr.write(error.stderr)
        return error.returncode or 1
    return 0


def gh_platform() -> str:
    system = {"Darwin": "macOS", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    machine = {"x86_64": "amd64", "AMD64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine()
    )
    if system is None or machine is None:
        message = f"gh の配布物が無い環境: {platform.system()} {platform.machine()}"
        raise GhUnavailableError(message)
    suffix = "tar.gz" if system == "linux" else "zip"
    return f"{system}_{machine}.{suffix}"


def fetch_gh(tools: Path = TOOLS_DIRECTORY) -> Path:
    """固定した版の gh の実体を返す。置き方と照らし方は fetch_actionlint と同じ。"""
    name = gh_platform()
    stem = name.split(".")[0]
    # Windows の配布物だけは、版と環境の名前のディレクトリを挟まない
    if stem.startswith("windows"):
        executable, member = "gh.exe", "bin/gh.exe"
    else:
        executable, member = "gh", f"gh_{GH_VERSION}_{stem}/bin/gh"
    target = tools / f"gh-{GH_VERSION}-{stem}" / executable
    if installed_binary_is_intact(target):
        return target
    url = GH_URL.format(version=GH_VERSION, platform=name)
    data = download(url)
    digest = hashlib.sha256(data).hexdigest()
    if digest != GH_SHA256[name]:
        message = f"gh の sha256 が固定値と異なる: {digest}（{url}）"
        raise GhUnavailableError(message)
    binary = extract_binary(data, name, member)
    target.parent.mkdir(parents=True, exist_ok=True)
    replace_atomically(target, binary, 0o755)
    replace_atomically(
        sidecar_of(target), hashlib.sha256(binary).hexdigest().encode("utf-8"), 0o644
    )
    return target


def run_scripts(workflow: Path) -> Iterator[str]:
    """ワークフローの各段の run の文字列。"""
    tree = YAML(typ="safe").load(workflow.read_text(encoding="utf-8"))
    for job in (tree.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if isinstance(step.get("run"), str):
                yield step["run"]


def substitute_placeholders(line: str) -> str:
    """gh 以外のコマンドの置き換えと変数を、仮の値に替える。"""
    previous = None
    while previous != line:
        previous = line
        # 内側から一つずつ替える。gh の置き換えは呼び出しとして残す
        line = re.sub(r"\$\((?!gh\s)[^()]*\)", PLACEHOLDER, line)
    return re.sub(
        r"\$\{(\w+)\}|\$(\w+)",
        lambda match: PLACEHOLDERS.get(match.group(1) or match.group(2), PLACEHOLDER),
        line,
    )


def gh_calls(script: str) -> Iterator[list[str]]:
    """run の文字列の中の gh の呼び出しを、gh を除いた引数の列で返す。"""
    for line in script.replace("\\\n", " ").splitlines():
        lexer = shlex.shlex(substitute_placeholders(line), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
        for position, word in enumerate(tokens):
            if word != "gh" or (position and tokens[position - 1] not in COMMAND_SEPARATORS):
                continue
            arguments: list[str] = []
            for argument in tokens[position + 1 :]:
                if argument in COMMAND_SEPARATORS:
                    break
                arguments.append(argument)
            yield arguments


def gh_environment(home: Path) -> dict[str, str]:
    """偽のトークンと行き止まりのプロキシだけを持つ環境。手元の gh の設定と認証は読ませない。"""
    environment = {
        "GH_TOKEN": "placeholder-token",
        "GH_CONFIG_DIR": str(home / "config"),
        "GH_PROMPT_DISABLED": "1",
        "GH_NO_UPDATE_NOTIFIER": "1",
        "NO_COLOR": "1",
        "HOME": str(home),
        "USERPROFILE": str(home),
        "HTTPS_PROXY": DEAD_PROXY,
        "HTTP_PROXY": DEAD_PROXY,
    }
    # Windows の名前の解決と通信は SYSTEMROOT が無いと動かない
    if "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return environment


def gh_call_error(executable: Path, arguments: list[str], home: Path) -> str | None:
    """gh の呼び出しが引数を通って通信の段に着いたら None、そうでなければ出力を返す。"""
    # 添付するファイルなどの引数が手元に無いと、gh は通信の前に止まる。パスに見える語は空で置く
    for argument in arguments:
        if re.fullmatch(r"[\w.-]+(/[\w.-]+)*", argument) and not argument.startswith("-"):
            path = home / "work" / argument
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.touch()
    try:
        run_tool([str(executable), *arguments], cwd=home / "work", env=gh_environment(home))
    except CommandError as error:
        output = error.stdout + error.stderr
    else:
        return "通信の段で断られるはずが、成功で終わった"
    if NETWORK_STAGE_MARKER in output and USAGE_MARKER not in output:
        return None
    return output


def run_gh_lines(directory: Path) -> int:
    try:
        executable = fetch_gh()
    except (ToolUnavailableError, OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        sys.stderr.write(f"gh を用意できない: {error}\n")
        return TOOL_UNAVAILABLE_EXIT_CODE
    calls = [
        (workflow.name, arguments)
        for workflow in sorted((directory / "workflows").glob("*.yml"))
        for script in run_scripts(workflow)
        for arguments in gh_calls(script)
    ]
    if not calls:
        sys.stderr.write("ワークフローに gh の行が一つも見つからない\n")
        return 1
    failed = 0
    for name, arguments in calls:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / "work").mkdir()
            output = gh_call_error(executable, arguments, home)
        if output is not None:
            failed = 1
            sys.stderr.write(f"{name}: gh {shlex.join(arguments)}\n{output}\n")
    return failed


DEFAULT_CHECKS: tuple[Callable[[Path], int], ...] = (run_actionlint, run_zizmor, run_gh_lines)


def composite_actions(directory: Path) -> list[Path]:
    """directory（ワークフローの置き場）と同じ .github 配下の composite action。"""
    github = directory.resolve().parent
    if github.name != DEFAULT_DIRECTORY.parent.name:
        # .github の外にある置き場では、親を再帰で探すと無関係な木まで拾う。ワークフローだけを見る
        return []
    found = [*github.rglob("action.yml"), *github.rglob("action.yaml")]
    # ワークフローの置き場の中は、ワークフローとして写し済み
    return sorted(path for path in found if path.parent != directory.resolve())


def audit(
    directory: Path,
    checks: Sequence[Callable[[Path], int]] = DEFAULT_CHECKS,
) -> int:
    with tempfile.TemporaryDirectory() as temporary:
        # zizmor はディレクトリの名前が .github/workflows でないとワークフローとして集めない。
        # 各検査には写しの .github を渡す。zizmor は .github 配下の composite action も集める
        github = Path(temporary) / DEFAULT_DIRECTORY.parent
        copy = github / DEFAULT_DIRECTORY.name
        copy.mkdir(parents=True)
        for workflow in sorted(directory.glob("*.yml")):
            (copy / workflow.name).write_bytes(workflow.read_bytes())
        for action in composite_actions(directory):
            destination = github / action.relative_to(directory.resolve().parent)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(action.read_bytes())
        # 一方が落ちても他方は走らせ、指摘を全部出す
        codes = [check(github) for check in checks]
    return next((code for code in codes if code), 0)


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("directory", nargs="?", type=Path, default=DEFAULT_DIRECTORY)
    return audit(parser.parse_args(argv).directory)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
