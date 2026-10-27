"""HEAD の木から非公開のパスを除いた写しを作り、公開してよい形かを段ごとに確かめる。

ゲートには入れない。公開リポジトリへ写す前に手で走らせる。

  export_public.py              一時の場所へ写して検査し、終わったら消す
  export_public.py --keep       一時の場所を消さずに残し、その場所を出す
  export_public.py --skip-check make check の段だけ飛ばす
  export_public.py --root DIR   DIR の git リポジトリを写す

段は、展開、除外、dependabot の差し替え、参照の検査、self-hosted の検査、入口の作り直し、
依存の導入、make check、ビルド、wheel の検査、help の検査、wheel だけの環境の検査の順。
除外するパスは .github/public-exclude.txt、残してよい参照は .github/public-references-allowed.txt、
写しに出してはいけない非公開の名前は .github/private-names.txt。
落ちた段の名前と原因を標準エラーへ出して終了コード 1。
"""

import argparse
import io
import os
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path

import tomlkit
from dev_launch import run_tool
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from workflow_rules import EXCLUDE_LIST, PUBLIC_COPY_KINDS, read_entries, violations

from jigane.git.process import CommandError, is_windows

ALLOWED_LIST = Path(".github/public-references-allowed.txt")
PRIVATE_NAMES_LIST = Path(".github/private-names.txt")
DEPENDABOT_PATH = Path(".github/dependabot.yml")
PULL_REQUEST_LIMIT_KEY = "open-pull-requests-limit"
Runner = Callable[..., str]
SKIPPED_DIRECTORIES = {".venv", "dist", ".git"}
ENTRYPOINTS_PATH = Path("entrypoints.toml")
# 写しの入口に出さない作業。除く一覧と非公開の名前の一覧が写しに無く、動かないため
PRIVATE_TASKS = ("export-public",)
# 写しの作業から外す行の目印。開発のゲートの設定で、写しは .git が無く、
# 利用者の git config にも書かせないため
PRIVATE_COMMAND_MARKER = "claude.gateCommand"


class StageError(Exception):
    def __init__(self, stage: str, detail: str) -> None:
        super().__init__(f"{stage}: {detail}")
        self.stage = stage
        self.detail = detail


def extract_head(root: Path, destination: Path, run: Runner = run_tool) -> None:
    archive = destination.parent / "head.tar"
    run(["git", "archive", "--format=tar", "-o", str(archive), "HEAD"], cwd=root)
    with tarfile.open(archive) as bundle:
        bundle.extractall(destination, filter="data")
    archive.unlink()


def remove_excluded(tree: Path) -> list[str]:
    removed: list[str] = []
    for entry in read_entries(tree / EXCLUDE_LIST):
        target = tree / entry.rstrip("/")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        else:
            # 一覧に残ったまま実体が無い行は一覧の古びなので、写しを作る段で止める
            raise StageError("除外", f"除外の一覧のパスが木に無い: {entry}")
        removed.append(entry)
    return removed


def render_yaml(yaml: YAML, document: CommentedMap) -> str:
    stream = io.StringIO()
    yaml.dump(document, stream)
    return stream.getvalue()


def silence_version_updates(tree: Path) -> int:
    """写しの dependabot.yml の各 update の PR の上限を 0 にする。

    作業用の版から作るので、update の足し引きが写しへそのまま届く。alert は PR を作らないため残る。
    """
    path = tree / DEPENDABOT_PATH
    if not path.exists():
        raise StageError("dependabot", f"写しに {DEPENDABOT_PATH} が無い")
    yaml = YAML()
    yaml.preserve_quotes = True
    # 書き換えない行の字下げと折り返しを保つ。今の dependabot.yml の書式に合わせた値
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    document = yaml.load(path.read_text(encoding="utf-8"))
    if not isinstance(document, CommentedMap) or "updates" not in document:
        raise StageError("dependabot", f"{DEPENDABOT_PATH} に updates が無い")
    updates = document["updates"]
    if not isinstance(updates, CommentedSeq) or not updates:
        raise StageError("dependabot", f"{DEPENDABOT_PATH} に update が無い")
    for update in updates:
        if not isinstance(update, CommentedMap):
            raise StageError("dependabot", f"{DEPENDABOT_PATH} の update がマッピングでない")
        update[PULL_REQUEST_LIMIT_KEY] = 0
    rendered = render_yaml(yaml, document)
    # 公開の木は全 update の PR を止める約束なので、書き出した結果を読み直して確かめる
    written = YAML(typ="safe").load(rendered)["updates"]
    silenced = sum(1 for update in written if update.get(PULL_REQUEST_LIMIT_KEY) == 0)
    if silenced != len(written):
        raise StageError(
            "dependabot", f"update {len(written)} 件のうち上限 0 になったのは {silenced} 件"
        )
    path.write_text(rendered, encoding="utf-8")
    return len(written)


def drop_private_tasks(text: str) -> tuple[str, list[str]]:
    """entrypoints.toml の文面から PRIVATE_TASKS の [[task]] を外す。外した作業の名前も返す。

    残す作業の commands からも、PRIVATE_COMMAND_MARKER を含む開発用の行を外す。
    """
    document = tomlkit.parse(text)
    # [[task]] が無い文書は空の列として扱い、外すものが無いまま返す
    tasks = document.get("task") or []
    dropped: list[str] = []
    # 後ろから外し、これから見る要素の添字を動かさない
    for index in reversed(range(len(tasks))):
        name = tasks[index].get("name")
        if name in PRIVATE_TASKS:
            dropped.insert(0, name)
            del tasks[index]
            continue
        commands = tasks[index].get("commands")
        # commands を持たない作業は外す行が無いので、そのまま残す
        if commands is None:
            continue
        for position in reversed(range(len(commands))):
            if PRIVATE_COMMAND_MARKER in commands[position]:
                del commands[position]
    return tomlkit.dumps(document), dropped


def regenerate_entrypoints(tree: Path, run: Runner, env: dict[str, str]) -> list[str]:
    """写しの entrypoints.toml から非公開の作業を外し、Makefile と justfile を作り直す。"""
    path = tree / ENTRYPOINTS_PATH
    if not path.exists():
        raise StageError("入口", f"写しに {ENTRYPOINTS_PATH} が無い")
    text, dropped = drop_private_tasks(path.read_text(encoding="utf-8"))
    if sorted(dropped) != sorted(PRIVATE_TASKS):
        raise StageError(
            "入口", f"外した作業 {dropped} が非公開の作業 {list(PRIVATE_TASKS)} と違う"
        )
    path.write_text(text, encoding="utf-8")
    command_stage(run, "入口", [sys.executable, "scripts/generate_entrypoints.py"], tree, env)
    return dropped


def reference_words(excluded: Sequence[str], private_names: Sequence[str]) -> list[str]:
    """残ってはいけない参照の語を作る。

    除外の一覧の各パスからはパスの形とドット区切りの形を、非公開の名前はそのまま語にする。
    """
    words: list[str] = []
    for entry in excluded:
        path = entry.rstrip("/")
        words.extend(word for word in (path, path.replace("/", ".")) if word not in words)
    words.extend(name for name in private_names if name not in words)
    return words


def find_unallowed_references(tree: Path, words: Sequence[str]) -> list[str]:
    allowed = {tuple(entry.split(":", 1)) for entry in read_entries(tree / ALLOWED_LIST)}
    found: list[str] = []
    for path in sorted(tree.rglob("*")):
        relative = path.relative_to(tree)
        if SKIPPED_DIRECTORIES & set(relative.parts) or not path.is_file():
            continue
        if relative in (ALLOWED_LIST, EXCLUDE_LIST):
            # 二つの一覧は、除くパスや残す参照を書くのが役目で、参照として数えると必ず落ちる
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # バイナリには行の概念が無く、参照の語を文として書く場所でもない
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            found.extend(
                f"{relative.as_posix()}:{number}: {word}"
                for word in words
                if word in line and (relative.as_posix(), word) not in allowed
            )
    return found


def find_self_hosted_workflows(tree: Path) -> list[str]:
    """写した木のワークフローのうち、GitHub のホストするランナー以外を指すもの。"""
    found: list[str] = []
    for path in sorted((tree / ".github" / "workflows").glob("*.y*ml")):
        relative = path.relative_to(tree).as_posix()
        found.extend(
            f"{relative}:{violation.line}: {violation}"
            for violation in violations(path.read_text("utf-8"))
            if violation.kind in PUBLIC_COPY_KINDS
        )
    return found


def wheel_excluded_members(wheel: Path, excluded: Sequence[str]) -> list[str]:
    """wheel の中身のうち、除外の一覧のディレクトリの下にあるもの。"""
    prefixes = tuple(entry for entry in excluded if entry.endswith("/"))
    with zipfile.ZipFile(wheel) as archive:
        return [name for name in archive.namelist() if name.startswith(prefixes)]


def command_stage(run: Runner, stage: str, args: list[str], cwd: Path, env: dict[str, str]) -> str:
    try:
        return run(args, cwd=cwd, env=env)
    except CommandError as error:
        raise StageError(stage, f"{' '.join(args)}\n{error.stdout}\n{error}") from error


def wheel_environment_stage(run: Runner, wheel: Path, work: Path, env: dict[str, str]) -> None:
    """wheel だけを入れた新しい環境で、PyPI から入れた利用者が最初に打つ三つを走らせる。

    開発の依存と写しの木は使わない。環境の変数は、JIGANE_HOME を一時の場所へ向け、
    UV_PROJECT_ENVIRONMENT を外す。
    """
    stage = "wheel だけの環境の検査"
    venv = work / "wheel-env"
    # 仮想環境の実行ファイルの置き場は、Windows だけ Scripts になる
    commands = venv / ("Scripts" if is_windows() else "bin")
    command_stage(run, stage, ["uv", "venv", str(venv)], work, env)
    command_stage(
        run,
        stage,
        ["uv", "pip", "install", "--python", str(commands / "python"), str(wheel)],
        work,
        env,
    )
    jigane = str(commands / "jigane")
    user_env = {**env, "JIGANE_HOME": str(work / "wheel-home")}
    # 写しの木の UV_PROJECT_ENVIRONMENT を引き継がない。引き継ぐと jigane が開発の環境を見る
    user_env.pop("UV_PROJECT_ENVIRONMENT", None)
    command_stage(run, stage, [jigane, "--help"], work, user_env)
    command_stage(run, stage, [jigane, "languages", "fetch", "--accept-terms"], work, user_env)
    repository = work / "user-repo"
    repository.mkdir()
    (repository / "sample.py").write_text("x = 1\n", encoding="utf-8")
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "add", "."],
        # 利用者の git の設定（名前、署名）に左右されないよう、commit の設定をその場で渡す
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "init",
        ],
    ):
        command_stage(run, stage, args, repository, user_env)
    command_stage(
        run, stage, [jigane, "new", "files", "--name", "smoke", "sample.py"], repository, user_env
    )


def check_in_temporary_repository(run: Runner, tree: Path, env: dict[str, str]) -> None:
    """写しを一時の git リポジトリにして make check を走らせ、終われば .git を消す。

    公開リポジトリの CI の checkout は git の木で、検査の中に index を読むものがある。
    残した写しを公開の木として確かめる段は、.git の無いことを求める。
    make check の最後の段は、未コミットの変更があると印を書かず落ちるので、コミットまで行う。
    """
    # 利用者の git の設定（名前、署名）に左右されないよう、commit の設定をその場で渡す
    identity = [
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "-c",
        "commit.gpgsign=false",
    ]
    try:
        for args in (
            ["git", "init", "-b", "main"],
            ["git", "add", "-A"],
            ["git", *identity, "commit", "-q", "-m", "export"],
        ):
            command_stage(run, "make check", args, tree, env)
        command_stage(run, "make check", ["make", "check"], tree, env)
    finally:
        shutil.rmtree(tree / ".git", ignore_errors=True)


def export(
    root: Path,
    work: Path,
    *,
    skip_check: bool,
    report: Callable[[str], None],
    run: Runner = run_tool,
) -> None:
    """run は外部コマンドを起動する関数。検査では偽物を渡し、ホストの道具に頼らず確かめる。"""
    tree = work / "tree"
    extract_head(root, tree, run)
    report("展開: 済")
    excluded = read_entries(tree / EXCLUDE_LIST)
    # 非公開の名前の一覧は写しから除くため、除く前に読む
    private_names = read_entries(tree / PRIVATE_NAMES_LIST)
    removed = remove_excluded(tree)
    report(f"除外: {len(removed)} 件")
    report(f"dependabot: version updates の PR を止めた update {silence_version_updates(tree)} 件")
    unallowed = find_unallowed_references(tree, reference_words(excluded, private_names))
    if unallowed:
        raise StageError("参照の検査", "許可の一覧に無い参照\n" + "\n".join(unallowed))
    report("参照の検査: 済")
    self_hosted = find_self_hosted_workflows(tree)
    if self_hosted:
        detail = "写しのワークフローに self-hosted が残っている\n" + "\n".join(self_hosted)
        raise StageError("self-hosted の検査", detail)
    report("self-hosted の検査: 済")
    dropped = regenerate_entrypoints(tree, run, {**os.environ})
    report(f"入口: 非公開の作業 {len(dropped)} 件を外して作り直した")
    home = work / "home"
    home.mkdir()
    env = {
        **os.environ,
        "UV_PROJECT_ENVIRONMENT": str(tree / ".venv"),
        "JIGANE_HOME": str(home),
    }
    # setup の段の中身を写さない。写すと、setup に段が足されたときに写しだけが古くなる
    command_stage(run, "依存の導入", ["make", "setup"], tree, env)
    report("依存の導入: 済")
    if skip_check:
        report("make check: 飛ばした")
    else:
        check_in_temporary_repository(run, tree, env)
        report("make check: 済")
    command_stage(run, "ビルド", ["uv", "build"], tree, env)
    wheels = sorted((tree / "dist").glob("*.whl"))
    if not wheels:
        raise StageError("ビルド", "dist に wheel が無い")
    report("ビルド: 済")
    leaked = [name for wheel in wheels for name in wheel_excluded_members(wheel, excluded)]
    if leaked:
        detail = "wheel に除外したはずの範囲が入っている\n" + "\n".join(leaked)
        raise StageError("wheel の検査", detail)
    report("wheel の検査: 済")
    help_args = ["uv", "run", "--locked", "jigane", "--help"]
    help_text = command_stage(run, "help の検査", help_args, tree, env)
    # jigane/ の下の除外は、サブコマンド名と同じ名前で help に出る
    names = [Path(entry.rstrip("/")).name for entry in excluded if entry.startswith("jigane/")]
    for name in names:
        if re.search(rf"\b{re.escape(name)}\b", help_text):
            raise StageError("help の検査", f"jigane --help に {name} が出ている")
    report("help の検査: 済")
    wheel_environment_stage(run, wheels[0], work, env)
    report("wheel だけの環境の検査: 済")


def main(argv: Sequence[str], run: Runner = run_tool) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="写す git リポジトリの場所")
    parser.add_argument("--keep", action="store_true", help="一時の場所を消さずに残す")
    parser.add_argument("--skip-check", action="store_true", help="make check の段を飛ばす")
    arguments = parser.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="jigane-export-")).resolve()

    def report(message: str) -> None:
        print(message, flush=True)

    try:
        export(
            arguments.root.resolve(),
            work,
            skip_check=arguments.skip_check,
            report=report,
            run=run,
        )
    except StageError as error:
        print(f"落ちた段: {error.stage}\n{error.detail}", file=sys.stderr)
        return 1
    except CommandError as error:
        print(f"落ちた段: 展開\n{error}", file=sys.stderr)
        return 1
    finally:
        if arguments.keep:
            print(f"残した場所: {work / 'tree'}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
