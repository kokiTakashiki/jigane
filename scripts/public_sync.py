"""書き出した木を、公開リポジトリのローカルの複製へ写す。

ゲートには入れない。export-public --keep が残した木を、署名つきのコミットの前に手で写す。

  public_sync.py --tree DIR                  DIR の内容を ~/jigane-public へ写す
  public_sync.py --tree DIR --public DIR2    写す先の複製を替える
  public_sync.py --tree DIR --version 0.1.8  木の pyproject の版が一致しなければ止まる

複製が次のどれかなら、何も変えずに止めて理由を出す。
git の作業木でない、main にいない、追跡下に未コミットの変更がある、
追跡外で無視されていないファイルがある、git pull --ff-only origin main が通らない。

消すのは複製の追跡下のファイルで木に無いものだけ。
複製の .gitignore が無視するもの（.venv、キャッシュ、dist の旧版など）は、
写す側にも消す側にも入れない。写した後の状態は git restore と git clean で戻せる。
"""

import argparse
import filecmp
import os
import shutil
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

from dev_launch import run_tool

from jigane.git.process import CommandError

DEFAULT_PUBLIC = Path("~/jigane-public").expanduser()
BRANCH = "main"
LISTED_LIMIT = 10
# 追跡外で無視されていないファイルの一覧。同じ引数の組が jigane/git/tree_files.py にある。
# 共有しないのは、jigane/ の起動の引数を check_egress が全要素が定数のリテラルに限るため
UNTRACKED_ARGS = ("ls-files", "-z", "--others", "--exclude-standard")
# 追跡下のファイルのうち、複製の .gitignore に当たるものの一覧
TRACKED_IGNORED_ARGS = ("ls-files", "-z", "--cached", "--ignored", "--exclude-standard")


class PreconditionError(Exception):
    pass


def git(public: Path, *args: str, stdin: str | None = None) -> str:
    return run_tool(["git", *args], cwd=public, stdin=stdin)


def git_lines(public: Path, *args: str) -> list[str]:
    output = git(public, *args)
    return [line for line in output.split("\0" if "-z" in args else "\n") if line]


def read_version(root: Path) -> str:
    try:
        document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        return str(document["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
        raise PreconditionError(f"{root / 'pyproject.toml'} から版を読めない: {error}") from error


def check_tree(tree: Path, version: str | None) -> None:
    if not tree.is_dir():
        raise PreconditionError(f"木が無い: {tree}")
    if (tree / ".git").exists():
        raise PreconditionError(f"木に .git がある。書き出した木ではない: {tree}")
    found = read_version(tree)
    if version is not None and version != found:
        raise PreconditionError(f"木の版 {found} が --version {version} と違う")
    print(f"木の版: {found}", flush=True)


def check_public(public: Path) -> None:
    try:
        top = git(public, "rev-parse", "--show-toplevel").strip()
    except (CommandError, FileNotFoundError) as error:
        raise PreconditionError(f"複製が git の作業木ではない: {public}") from error
    if Path(top).resolve() != public.resolve():
        raise PreconditionError(f"複製の根が {top} で、{public} ではない")
    branch = git(public, "branch", "--show-current").strip()
    if branch != BRANCH:
        raise PreconditionError(f"複製が {BRANCH} にいない（今は {branch or 'detached'}）")
    changed = git_lines(public, "status", "--porcelain", "--untracked-files=no")
    if changed:
        raise PreconditionError("追跡下に未コミットの変更がある\n" + "\n".join(changed))
    untracked = git_lines(public, *UNTRACKED_ARGS)
    if untracked:
        raise PreconditionError(
            "追跡外で無視されていないファイルがある。消すか追跡するかを人間が決める\n"
            + "\n".join(untracked)
        )
    tracked_ignored = git_lines(public, *TRACKED_IGNORED_ARGS)
    if tracked_ignored:
        # 無視のディレクトリへ探索が入らず、下の追跡下のファイルが「木に無い」扱いで消えるため
        raise PreconditionError(
            "追跡下のファイルが複製の .gitignore で無視されている。消す前に人間が決める\n"
            + "\n".join(tracked_ignored)
        )
    try:
        git(public, "pull", "--ff-only", "origin", BRANCH)
    except CommandError as error:
        message = f"git pull --ff-only origin {BRANCH} が通らない\n{error}"
        raise PreconditionError(message) from error
    print(f"複製の版: {read_version(public)}", flush=True)


def ignored_among(public: Path, paths: Sequence[str]) -> set[str]:
    """複製の .gitignore が無視する path の集合。ディレクトリは末尾に / を付けて渡す。"""
    if not paths:
        return set()
    try:
        output = git(public, "check-ignore", "--stdin", "-z", stdin="\0".join(paths) + "\0")
    except CommandError as error:
        # check-ignore は、無視されるものが 1 つも無いときだけ終了コード 1 を返す
        if error.returncode == 1:
            return set()
        raise
    return {path for path in output.split("\0") if path}


def tree_files(public: Path, tree: Path) -> list[str]:
    """木のファイルのうち、複製の .gitignore が無視しないもの。

    書き出した木は .venv や dist を持つので、全部を数えず、無視されるディレクトリは入らずに切る。
    """
    found: list[str] = []
    frontier = [""]
    while frontier:
        entries: dict[str, bool] = {}
        for directory in frontier:
            for entry in os.scandir(tree / directory):
                relative = f"{directory}{entry.name}"
                if relative == ".git":
                    continue
                entries[relative] = entry.is_dir(follow_symlinks=False)
        ignored = ignored_among(
            public, [f"{path}/" if is_dir else path for path, is_dir in entries.items()]
        )
        frontier = []
        for path, is_dir in sorted(entries.items()):
            if (f"{path}/" if is_dir else path) in ignored:
                continue
            if is_dir:
                frontier.append(f"{path}/")
            else:
                found.append(path)
    return sorted(found)


def differs(source: Path, destination: Path) -> bool:
    if source.is_symlink() or destination.is_symlink():
        return not (
            source.is_symlink()
            and destination.is_symlink()
            and source.readlink() == destination.readlink()
        )
    same_mode = (source.stat().st_mode & 0o111) == (destination.stat().st_mode & 0o111)
    return not (same_mode and filecmp.cmp(source, destination, shallow=False))


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 書き込み先が別の種類（リンクとファイル）だと copy が壊れるので、先に外す
    if destination.is_symlink() or destination.exists():
        destination.unlink()
    # 更新時刻は引き継がない。同じ大きさで古い時刻が付くと、Windows の git が変更なしと見なす
    shutil.copy(source, destination, follow_symlinks=False)


def remove_empty_parents(public: Path, relative: str) -> None:
    for parent in Path(relative).parents:
        if parent == Path():
            break
        try:
            (public / parent).rmdir()
        except OSError:
            # 無視されたファイルが残るディレクトリは、消さないのが正しい
            break


def sync(
    tree: Path,
    public: Path,
    *,
    version: str | None,
) -> None:
    check_tree(tree, version)
    check_public(public)
    tracked = set(git_lines(public, "ls-files", "-z"))
    wanted = tree_files(public, tree)
    deleted = sorted(tracked - set(wanted))
    created = [path for path in wanted if path not in tracked]
    updated = [path for path in wanted if path in tracked and differs(tree / path, public / path)]
    print(f"消す（追跡下で木に無い）: {len(deleted)} 件", flush=True)
    for path in deleted[:LISTED_LIMIT]:
        print(f"  {path}", flush=True)
    if len(deleted) > LISTED_LIMIT:
        print(f"  ほか {len(deleted) - LISTED_LIMIT} 件", flush=True)
    print(f"新規: {len(created)} 件、更新: {len(updated)} 件", flush=True)
    for path in deleted:
        (public / path).unlink()
        remove_empty_parents(public, path)
    for path in [*created, *updated]:
        copy_file(tree / path, public / path)
    status = git_lines(public, "status", "--porcelain")
    print(f"git status --porcelain: {len(status)} 件", flush=True)
    print(git(public, "diff", "--stat").rstrip(), flush=True)


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--tree", type=Path, required=True, help="export-public --keep の木")
    parser.add_argument("--public", type=Path, default=DEFAULT_PUBLIC, help="公開の複製の場所")
    parser.add_argument("--version", help="木の pyproject の版がこれと一致することを求める")
    arguments = parser.parse_args(argv)
    try:
        sync(
            arguments.tree.expanduser().resolve(),
            arguments.public.expanduser().resolve(),
            version=arguments.version,
        )
    except PreconditionError as error:
        print(f"止めた: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
