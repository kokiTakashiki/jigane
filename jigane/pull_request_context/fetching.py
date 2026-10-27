import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from jigane.git import process
from jigane.judgment_map.classification import FilePair
from jigane.pull_request_context import gh
from jigane.storage import store

_URL_PATTERN = re.compile(
    r"^https://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/pull/(\d+)(?:/.*)?$"
)


@dataclass(frozen=True)
class PullRequestMetadata:
    owner: str
    repo: str
    number: int
    url: str
    title: str
    body: str
    base_sha: str
    head_sha: str
    author: str


def parse_pull_request_url(url: str) -> tuple[str, str, int]:
    match = _URL_PATTERN.match(url.strip())
    if match is None:
        raise ValueError(f"PR の URL の形が不正です: {url!r}")
    return match[1], match[2], int(match[3])


def fetch_metadata(owner: str, repo: str, number: int) -> PullRequestMetadata:
    output = gh.view_pull_request(owner, repo, number)
    data = json.loads(output)
    return PullRequestMetadata(
        owner=owner,
        repo=repo,
        number=data["number"],
        url=data["url"],
        title=data["title"],
        body=data["body"],
        base_sha=data["baseRefOid"],
        head_sha=data["headRefOid"],
        author=data["author"]["login"],
    )


def ensure_repository_cache(owner: str, repo: str) -> Path:
    # URL の構文を通った名前が置き場の外へ出ないよう、パスを作るこの段でも名前の規則で確かめる
    for name in (owner, repo):
        if not store.COMPONENT_PATTERN.fullmatch(name):
            raise ValueError(f"リポジトリの名前が不正です: {name!r}")
    cache_dir = store.data_home() / "repositories" / owner / f"{repo}.git"
    if cache_dir.exists():
        return cache_dir
    # gh repo clone は中で git を呼び、git が無いと gh の失敗として現れる。置き場を作る前に
    # git の有無を確かめ、無ければ「必要なコマンドが見つからない」として止める
    process.run_command(["git", "--version"])
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    # 最終の名前へは clone が終わってから置き換えで載せる。途中で失敗しても、作りかけが
    # 最終の名前に現れて以後の git の実行を壊れたリポジトリへ向けることがない。
    # 置き換えが同じファイルシステムの中で閉じるよう、一時の置き場は同じ親に置く
    partial_dir = cache_dir.with_name(f"{cache_dir.name}.partial-{os.getpid()}")
    shutil.rmtree(partial_dir, ignore_errors=True)
    try:
        gh.clone_repository(owner, repo, str(partial_dir))
        try:
            partial_dir.replace(cache_dir)
        except OSError:
            # 空でない既存のディレクトリへの置き換えは失敗する。同時に走った別のプロセスが先に
            # 完成させた場合なので、そちらを使う。それ以外の失敗はそのまま伝える
            if not cache_dir.exists():
                raise
    finally:
        shutil.rmtree(partial_dir, ignore_errors=True)
    return cache_dir


def fetch_pull_request(git_dir: Path, metadata: PullRequestMetadata) -> str:
    """二つのコミットを取得し、変更前のツリーとなる merge-base のコミットを返す。"""
    process.run_command(["git", "fetch", "origin", metadata.head_sha], cwd=git_dir)
    try:
        process.run_command(["git", "fetch", "origin", metadata.base_sha], cwd=git_dir)
    except process.CommandError:
        # base ブランチが進んで base_sha が到達不能になると直接は取れない。
        # PR の ref は作成時点の base を履歴に含む
        process.run_command(
            ["git", "fetch", "origin", f"pull/{metadata.number}/head"], cwd=git_dir
        )
    # base ブランチの後の進みを含めないため、変更前は base の先頭でなく merge-base
    merge_base = process.run_command(
        ["git", "merge-base", metadata.base_sha, metadata.head_sha], cwd=git_dir
    )
    return merge_base.strip()


def extract_tree(git_dir: Path, commit: str, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    # 標準出力を text で受けると tar のバイト列が壊れるため、ファイルへ書かせる
    with tempfile.TemporaryDirectory() as temporary:
        archive_path = Path(temporary) / "tree.tar"
        process.run_command(
            ["git", "archive", "--format=tar", "-o", str(archive_path), commit],
            cwd=git_dir,
        )
        try:
            with tarfile.open(archive_path) as archive:
                archive.extractall(destination, filter="data")
        except tarfile.ReadError:
            # 空の木の git archive は tar として読めない。木の空は ls-tree の出力で確かめる
            if process.run_command(["git", "ls-tree", commit], cwd=git_dir).strip():
                raise
            # 木が空なら、作成済みの空のディレクトリがそのまま写しになる


def parse_name_status(output: str) -> list[FilePair]:
    tokens = output.split("\0")
    pairs: list[FilePair] = []
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index][0]
        if status in ("R", "C"):
            pairs.append(FilePair(tokens[index + 1], tokens[index + 2]))
            index += 3
            continue
        path = tokens[index + 1]
        index += 2
        if status == "A":
            pairs.append(FilePair(None, path))
        elif status == "D":
            pairs.append(FilePair(path, None))
        elif status in ("M", "T"):
            pairs.append(FilePair(path, path))
        else:
            raise ValueError(f"想定外の変更の種類です: {status!r}")
    return pairs


def changed_file_pairs(git_dir: Path, base_commit: str, head_commit: str) -> list[FilePair]:
    output = process.run_command(
        ["git", "diff", "--name-status", "-M", "-z", base_commit, head_commit],
        cwd=git_dir,
    )
    return parse_name_status(output)
