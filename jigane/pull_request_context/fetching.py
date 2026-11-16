import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from jigane.git import process
from jigane.git.command_line import GitSubcommand, build_git_command
from jigane.git.invocation import git_version
from jigane.git.revision import CommitSha, parse_commit_sha
from jigane.judgment_map.classification import FilePair
from jigane.pull_request_context import gh
from jigane.storage import store
from jigane.storage.real_directory import require_real_directory
from jigane.text.json_input import loads_bounded

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
    base_sha: CommitSha
    head_sha: CommitSha
    author: str


def parse_pull_request_url(url: str) -> tuple[str, str, int]:
    match = _URL_PATTERN.match(url.strip())
    if match is None:
        raise ValueError(f"PR の URL の形が不正です: {url!r}")
    return match[1], match[2], int(match[3])


def fetch_metadata(owner: str, repo: str, number: int) -> PullRequestMetadata:
    output = gh.view_pull_request(owner, repo, number)
    try:
        data = cast("dict[str, Any]", loads_bounded(output, gh.MAX_RESPONSE_NESTING))
    except ValueError:
        # 出力の中身は端末へ出さない。巨大な値や制御文字が混じりうる
        raise store.ReviewError(
            "gh pr view（PR のメタデータの取得）の出力を JSON として読めませんでした。"
            "構文に誤りがあるか、入れ子が深すぎます。"
            "gh を新しいバージョンに更新してから、もう一度実行してください。"
        ) from None
    # gh の応答の SHA の形を確かめる唯一の点。以後は型が形を保証する
    try:
        base_sha = parse_commit_sha("baseRefOid", str(data["baseRefOid"]))
        head_sha = parse_commit_sha("headRefOid", str(data["headRefOid"]))
    except ValueError as error:
        raise store.ReviewError(str(error)) from None
    return PullRequestMetadata(
        owner=owner,
        repo=repo,
        number=data["number"],
        url=data["url"],
        title=data["title"],
        body=data["body"],
        base_sha=base_sha,
        head_sha=head_sha,
        author=data["author"]["login"],
    )


def repository_cache_path(owner: str, repo: str) -> Path:
    """PR のために複製したリポジトリの場所。複製の有無は見ない。"""
    # URL の構文を通った名前が置き場の外へ出ないよう、パスを作るこの段でも名前の規則で確かめる
    for name in (owner, repo):
        if not store.COMPONENT_PATTERN.fullmatch(name):
            raise ValueError(f"リポジトリの名前が不正です: {name!r}")
    return store.data_home() / "repositories" / owner / f"{repo}.git"


def _require_real_cache_directories(cache_dir: Path) -> None:
    # 置き場の根は利用者が選ぶので対象にしない。根の下の三段が symlink で外へ出ないことを確かめる
    for directory in (cache_dir.parent.parent, cache_dir.parent, cache_dir):
        require_real_directory(directory)


def ensure_repository_cache(owner: str, repo: str) -> Path:
    """複製の場所を返す。無ければ作る。

    残る競合として、置き場の検査から clone までの隙間に差し替えられる場合は防げない。
    """
    cache_dir = repository_cache_path(owner, repo)
    _require_real_cache_directories(cache_dir)
    if cache_dir.exists():
        return cache_dir
    # gh repo clone は中で git を呼び、git が無いと gh の失敗として現れる。置き場を作る前に
    # git の有無を確かめ、無ければ「必要なコマンドが見つからない」として止める
    process.run_command(git_version())
    store.ensure_data_home()
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    # 検査と作成の間に差し替えられた場合に備え、作った後でもう一度確かめてから複製する
    _require_real_cache_directories(cache_dir)
    # 最終の名前へは clone が終わってから置き換えで載せる。途中で失敗しても、作りかけが
    # 最終の名前に現れて以後の git の実行を壊れたリポジトリへ向けることがない。
    # 置き換えが同じファイルシステムの中で閉じるよう、一時の置き場は同じ親に置く
    # 名前は予測できない新規のディレクトリにする。pid のような予測できる名前だと、外部が置いた
    # symlink の先へ clone が書きうる。gh repo clone は空の既存ディレクトリへも複製できる
    partial_dir = Path(tempfile.mkdtemp(dir=cache_dir.parent, prefix=f"{cache_dir.name}.partial-"))
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


def fetch_pull_request(git_dir: Path, metadata: PullRequestMetadata) -> CommitSha:
    """二つのコミットを取得し、変更前のツリーとなる merge-base のコミットを返す。"""
    head = metadata.head_sha
    base = metadata.base_sha
    process.run_command(
        build_git_command(GitSubcommand.FETCH, fixed_values=["origin"], revisions=[head]),
        cwd=git_dir,
    )
    try:
        process.run_command(
            build_git_command(GitSubcommand.FETCH, fixed_values=["origin"], revisions=[base]),
            cwd=git_dir,
        )
    except process.CommandError:
        # base ブランチが進んで base_sha が到達不能になると直接は取れない。
        # PR の ref は作成時点の base を履歴に含む
        process.run_command(
            build_git_command(
                GitSubcommand.FETCH,
                fixed_values=["origin", f"pull/{metadata.number}/head"],
            ),
            cwd=git_dir,
        )
    # base ブランチの後の進みを含めないため、変更前は base の先頭でなく merge-base
    merge_base = process.run_command(
        build_git_command(GitSubcommand.MERGE_BASE, revisions=[base, head]), cwd=git_dir
    )
    # git の出力を読むこの点で、merge-base の形を一度だけ確かめる
    try:
        return parse_commit_sha("merge-base", merge_base.strip())
    except ValueError as error:
        raise store.ReviewError(str(error)) from None


_FILE_TYPE_MASK = 0o170000
_REGULAR_FILE_TYPE = 0o100000
_SYMLINK_TYPE = 0o120000
_OWNER_EXECUTE = 0o100
_BATCH_HEADER_FIELDS = 3
_MAX_LINK_HOPS = 40


@dataclass(frozen=True)
class _TreeEntry:
    """起点の木の 1 項目。mode は git の 8 進、content はコミット済みのブロブのバイト列。"""

    mode: int
    path: str
    content: bytes


def _tree_listing(git_dir: Path, tree: CommitSha) -> list[tuple[int, str, str]]:
    """木の (mode, オブジェクト名, パス) を返す。サブモジュールの項目は含めない。"""
    listing = process.run_command_bytes(
        build_git_command(
            GitSubcommand.LS_TREE, options=["-r", "-z", "--full-tree"], revisions=[tree]
        ),
        cwd=git_dir,
    )
    entries: list[tuple[int, str, str]] = []
    for record in listing.split(b"\0"):
        if not record:
            continue
        header, raw_path = record.split(b"\t", 1)
        mode, object_type, object_id = header.decode("ascii").split(" ")
        # 160000（サブモジュール）は commit 型で、今の archive と同じく展開しない
        if object_type == "blob":
            # UTF-8 として読めないパス名は、OS が受ける形（POSIX は surrogateescape）に戻す
            try:
                path = os.fsdecode(raw_path)
            except UnicodeDecodeError:
                # Windows のファイル名は UTF-8 のバイト列でなく、この名前を書き出せない
                raise store.ReviewError(
                    f"このファイルシステムで扱えないパス名が含まれています: {raw_path!r}"
                ) from None
            entries.append((int(mode, 8), object_id, path))
    return entries


def _read_blobs(git_dir: Path, object_ids: list[str]) -> dict[str, bytes]:
    """ブロブの中身を、属性（filter と textconv）を適用せずに読む。"""
    unique = list(dict.fromkeys(object_ids))
    if not unique:
        return {}
    output = process.run_command_bytes(
        build_git_command(GitSubcommand.CAT_FILE, options=["--batch"]),
        cwd=git_dir,
        stdin="".join(f"{object_id}\n" for object_id in unique).encode("ascii"),
    )
    blobs: dict[str, bytes] = {}
    position = 0
    for object_id in unique:
        line_end = output.index(b"\n", position)
        fields = output[position:line_end].decode("ascii").split(" ")
        if len(fields) != _BATCH_HEADER_FIELDS or fields[0] != object_id or fields[1] != "blob":
            raise store.ReviewError(f"ブロブを読めませんでした: {object_id}")
        size = int(fields[2])
        blobs[object_id] = output[line_end + 1 : line_end + 1 + size]
        position = line_end + 1 + size + 1
    return blobs


def _prefetch_missing_blobs(git_dir: Path, tree: CommitSha) -> None:
    """blobless の複製に無いブロブを、origin から 1 回の fetch でまとめて取る。

    cat-file --batch は無いブロブを 1 件ずつ遅延で fetch し、ファイルの数だけ通信が起きる。
    rev-list の --missing=print は遅延の fetch を起こさずに、無いオブジェクトを `?` の行で返す。
    --no-walk で履歴を歩かず、起点のコミットの木に限る。
    """
    # --objects の行はパス名を含み、UTF-8 として読めないことがあるのでバイト列で受ける
    listing = process.run_command_bytes(
        build_git_command(
            GitSubcommand.REV_LIST,
            options=["--objects", "--no-walk", "--missing=print"],
            revisions=[tree],
        ),
        cwd=git_dir,
    )
    missing = [line[1:].decode("ascii") for line in listing.splitlines() if line.startswith(b"?")]
    if not missing:
        # 全部が手元にある。fetch を起動しない
        return
    # git が遅延の fetch で中から打つ形と同じ引数を、まとめて 1 回で渡す
    # 文字列の stdin は Windows で改行が \r\n になり、git が refspec として拒む
    process.run_command_bytes(
        build_git_command(
            GitSubcommand.FETCH,
            global_options=["-c", "fetch.negotiationAlgorithm=noop"],
            options=[
                "--no-tags",
                "--no-write-fetch-head",
                "--recurse-submodules=no",
                "--filter=blob:none",
                "--stdin",
            ],
            fixed_values=["origin"],
        ),
        cwd=git_dir,
        stdin="".join(f"{object_id}\n" for object_id in missing).encode("ascii"),
    )


def _read_tree(git_dir: Path, tree: CommitSha) -> list[_TreeEntry]:
    """起点の木の項目とブロブを読む。ファイルシステムには触れない。"""
    _prefetch_missing_blobs(git_dir, tree)
    listing = _tree_listing(git_dir, tree)
    blobs = _read_blobs(git_dir, [object_id for _, object_id, _ in listing])
    return [_TreeEntry(mode, path, blobs[object_id]) for mode, object_id, path in listing]


def _checked_target(destination: Path, path: str) -> Path:
    """展開先の外へ出るパスを、tar の data フィルタと同じ種類の例外で拒む。"""
    member = tarfile.TarInfo(path)
    root = os.path.realpath(destination)
    target = os.path.realpath(Path(root) / path)
    if Path(path).is_absolute() or os.path.commonpath([root, target]) != root:
        raise tarfile.OutsideDestinationError(member, target)
    return Path(root) / path


def _link_escapes(links: dict[str, str], path: str) -> bool:
    """項目の表だけでリンクをたどり、展開先の外へ出るかを返す。

    Windows の解決は `..` を字句で畳むことがあり、後のリンクで向きの変わるリンクを
    os.path.realpath では見抜けない。リンク先の区切りはバックスラッシュも含め、広く拒む側に倒す。
    """
    pending = path.split("/")
    resolved: list[str] = []
    hops = 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not resolved:
                return True
            resolved.pop()
            continue
        candidate = "/".join([*resolved, part])
        if candidate not in links:
            resolved.append(part)
            continue
        hops += 1
        if hops > _MAX_LINK_HOPS:
            # 長い鎖の先で外へ出る手を見逃さないよう、たどり切れないリンクは拒む
            return True
        pending = [*re.split(r"[/\\]", links[candidate]), *pending]
    return False


def _write_tree(destination: Path, entries: list[_TreeEntry]) -> None:
    """項目を展開先へ書く。外へ出るパスとシンボリックリンクは、全体を拒む。

    シンボリックリンクは通常のファイルを書き終えた後にまとめて作り、作り終えてから
    すべての解決先を確かめ直す。後から作る項目で先のリンクの解決先が変わるため、
    ls-tree の順には頼れない。
    """
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    try:
        _write_entries(destination, entries)
    except Exception as rejection:
        # 拒んだ後に、外を指すリンクを含む途中の結果を残さない。ディレクトリ自体は残す
        try:
            for child in destination.iterdir():
                if child.is_symlink() or child.is_file():
                    child.unlink()
                else:
                    shutil.rmtree(child)
        except OSError as cleanup_failure:
            # 後始末の失敗で、元の拒否の例外を隠さない
            rejection.add_note(f"展開先の後始末にも失敗しました: {cleanup_failure}")
        raise


def _write_entries(destination: Path, entries: list[_TreeEntry]) -> None:
    links: list[tuple[Path, str]] = []
    link_targets: dict[str, str] = {}
    for entry in entries:
        file_type = entry.mode & _FILE_TYPE_MASK
        target = _checked_target(destination, entry.path)
        if file_type == _REGULAR_FILE_TYPE:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(entry.content)
            # 古い履歴の 100664 なども、実行ビットだけを見て 644 か 755 に丸める
            target.chmod(0o755 if entry.mode & _OWNER_EXECUTE else 0o644)
        elif file_type != _SYMLINK_TYPE:
            raise store.ReviewError(f"想定外のファイルの種類です: {entry.mode:o} {entry.path}")
    for entry in entries:
        if entry.mode & _FILE_TYPE_MASK != _SYMLINK_TYPE:
            continue
        link_target = entry.content.decode("utf-8", errors="surrogateescape")
        if Path(link_target).is_absolute():
            raise tarfile.AbsoluteLinkError(tarfile.TarInfo(entry.path))
        link = _checked_target(destination, entry.path)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(link_target)
        links.append((link, entry.path))
        link_targets[entry.path] = link_target
    _check_links(destination, links, link_targets)


def _check_links(
    destination: Path, links: list[tuple[Path, str]], link_targets: dict[str, str]
) -> None:
    for _, path in links:
        if _link_escapes(link_targets, path):
            raise tarfile.LinkOutsideDestinationError(tarfile.TarInfo(path), path)
    root = os.path.realpath(destination)
    for link, path in links:
        resolved = os.path.realpath(link)
        if os.path.commonpath([root, resolved]) != root:
            raise tarfile.LinkOutsideDestinationError(tarfile.TarInfo(path), resolved)


def extract_tree(git_dir: Path, tree: CommitSha, destination: Path) -> None:
    """木をコミット済みのブロブのバイト列のまま書き出す。filter も textconv も起動しない。

    git archive は filter の smudge を起動しうるので、ls-tree と cat-file --batch で読む。
    """
    _write_tree(destination, _read_tree(git_dir, tree))


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


def changed_file_pairs(git_dir: Path, base: CommitSha, head: CommitSha) -> list[FilePair]:
    output = process.run_command(
        build_git_command(
            GitSubcommand.DIFF,
            options=["--name-status", "-M", "-z"],
            revisions=[base, head],
        ),
        cwd=git_dir,
    )
    return parse_name_status(output)
