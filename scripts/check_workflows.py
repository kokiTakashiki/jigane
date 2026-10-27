"""GitHub Actions のワークフローを actionlint と zizmor で静的に検査する。make lint から走る。

  check_workflows.py [DIR]   DIR（既定は .github/workflows）の *.yml と、
                             DIR の親が .github のときその配下の action.yml / action.yaml
                             （composite action）を検査する

actionlint は文法と式、zizmor は安全を見る。zizmor はネットワークを使わず（--offline）、
pedantic の水準で見る。actionlint はワークフローだけを読み、action.yml は読めないので渡さない。
zizmor にはワークフローと composite action の両方を渡す。
actionlint は版と sha256 を固定した配布物を、初回だけ GitHub のリリースから取得して .tools/ に
置く（照合に失敗したら使わずに落とす）。実体を用意できないときの終了コードは 4 で、指摘と区別
する。公開前の ci.yml は `on:` をコメントアウトしており、
そのままでは zizmor が入力として読めない。一時の置き場へ写しを作り、コメントアウトした `on:`
の塊だけを戻して検査する。元のファイルは変えない。指摘があればその終了コードで終わる。
"""

import argparse
import hashlib
import io
import platform
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path

from dev_launch import run_tool

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
# actionlint 自身は 0〜3 で終わる。4 は実体を用意できなかったことを指し、指摘とは区別する
ACTIONLINT_UNAVAILABLE_EXIT_CODE = 4
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


class ActionlintUnavailableError(RuntimeError):
    """actionlint の実体を用意できなかった。ワークフローの指摘ではない。"""


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
            message = f"actionlint の配布物に {executable} が無い"
            raise ActionlintUnavailableError(message)
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
    except (ActionlintUnavailableError, OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        sys.stderr.write(f"actionlint を用意できない: {error}\n")
        return ACTIONLINT_UNAVAILABLE_EXIT_CODE
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
        run_tool(["zizmor", "--offline", "--no-progress", "--pedantic", *config, str(directory)])
    except CommandError as error:
        sys.stdout.write(error.stdout)
        sys.stderr.write(error.stderr)
        return error.returncode or 1
    return 0


DEFAULT_CHECKS: tuple[Callable[[Path], int], ...] = (run_actionlint, run_zizmor)


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
