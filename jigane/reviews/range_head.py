"""範囲のレビューが git を走らせる場所と、refresh が回を作る先端の選び方。"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Protocol, assert_never

from jigane.git import process
from jigane.git.command_line import GitSubcommand, build_git_command
from jigane.git.revision import parse_commit_sha, resolve_commit_sha
from jigane.storage.review_error import ReviewError

_RELATIVE_SUFFIX = re.compile(r"[~^].*$")
_BRANCH_PREFIX = "refs/heads/"


def run_git(root: Path, *arguments: str) -> str:
    return process.run_command(["git", *arguments], cwd=root)


def git_succeeds(root: Path, *arguments: str) -> bool:
    try:
        run_git(root, *arguments)
    except process.CommandError as error:
        if error.missing_executable:
            raise
        return False
    return True


def resolve_commit(root: Path, revision: str) -> str:
    try:
        return str(resolve_commit_sha(root, revision))
    except process.CommandError as error:
        if error.missing_executable:
            # git が無いことは refs の誤りではない。「必要なコマンドが見つからない」として伝える
            raise
        raise ReviewError(f"refs を解けません: {revision}") from error


@dataclass(frozen=True)
class RangeRefs:
    """範囲の文字列を左右の refs 名に割った値。右側を省いた範囲の head は HEAD。"""

    base: str
    head: str


def split_range(range_text: str) -> RangeRefs:
    base, separator, head = range_text.partition("..")
    if not separator or not base or head.startswith("."):
        raise ReviewError(f"範囲は <base>..<head> の形で指定します: {range_text!r}")
    return RangeRefs(base, head or "HEAD")


class TrackedBranchKind(StrEnum):
    """範囲の先端を、どういう根拠で枝として追うか。"""

    # 範囲の先端の書き方が、枝の名前だった
    NAMED_BRANCH = "named_branch"
    # 先端の SHA が、作ったときの作業ディレクトリの今の枝の先端と一致した
    CURRENT_BRANCH = "current_branch"


@dataclass(frozen=True)
class TrackedBranch:
    """refresh が範囲の先端として追う枝。"""

    kind: TrackedBranchKind
    # 枝の名前（refs/heads/ を除く）
    branch: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "branch": self.branch}

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "TrackedBranch":
        """保存した値から戻す。形が合わないときは KeyError、TypeError、ValueError。"""
        return TrackedBranch(TrackedBranchKind(raw["kind"]), raw["branch"])


# 範囲の先端の追い方。追わないときは UNTRACKED
HeadTracking = TrackedBranch | None
UNTRACKED: HeadTracking = None


def _full_reference_name(root: Path, written: str) -> str:
    """先端の書き方から参照の完全な名前を引く。SHA と解けない書き方は空を返す。"""
    try:
        output = run_git(root, "rev-parse", "--symbolic-full-name", "--end-of-options", written)
    except process.CommandError as error:
        if error.missing_executable:
            raise
        return ""
    # 出力は --end-of-options を写すので、その行を除く
    names = [line for line in output.splitlines() if line and line != "--end-of-options"]
    return names[-1] if names else ""


def head_tracking_of(root: Path, range_text: str, head_sha: str) -> HeadTracking:
    """範囲の先端の追い方を決める。

    枝の名前で書いた先端は NAMED_BRANCH。そうでなければ、作業ディレクトリの今の枝の先端が
    解いた先端と一致するときだけ CURRENT_BRANCH。HEAD や reflog の書き方と、今の枝の先端と
    一致しない SHA は追わない。
    """
    head = split_range(range_text).head
    # HEAD、reflog と upstream の書き方は、作業ディレクトリがあれば範囲の文字列の解き直しで動く。
    # 作業ディレクトリが無いときに追う枝は決められないので追わない
    if "@{" in head or _RELATIVE_SUFFIX.sub("", head) == "HEAD":
        return UNTRACKED
    full_name = _full_reference_name(root, head)
    if full_name.startswith(_BRANCH_PREFIX):
        return TrackedBranch(
            TrackedBranchKind.NAMED_BRANCH, full_name.removeprefix(_BRANCH_PREFIX)
        )
    try:
        current = run_git(root, "symbolic-ref", "--quiet", "HEAD").strip()
    except process.CommandError as error:
        if error.missing_executable:
            raise
        # detached の作業ディレクトリは、追う枝を持たない
        return UNTRACKED
    if resolve_commit(root, current) == head_sha:
        return TrackedBranch(
            TrackedBranchKind.CURRENT_BRANCH, current.removeprefix(_BRANCH_PREFIX)
        )
    return UNTRACKED


def _head_is_fixed(git: "GitQueries", range_text: str) -> bool:
    """範囲の先端が、解き直しても動かない参照かを返す。

    枝（heads/x や refs/heads/x の書き方を含む）、追跡の枝、HEAD、reflog と upstream の書き方
    （HEAD@{1}、@{u}）、およびそれらからの相対（HEAD~1 など）は、
    解き直すたびに動くので固定ではない。
    SHA、タグ、解けない参照は固定とみなす。
    """
    head = split_range(range_text).head
    if "@{" in head:
        return False
    # 相対の書き方は基点の参照で判定する
    base = _RELATIVE_SUFFIX.sub("", head)
    if base == "HEAD":
        return False
    full_name = git.full_reference_name(base)
    if not full_name:
        return True
    return not full_name.startswith((_BRANCH_PREFIX, "refs/remotes/", "HEAD"))


class GitPlaceKind(StrEnum):
    """refresh が git を走らせる場所の種類。"""

    WORKING_DIRECTORY = "working_directory"
    COMMON_DIRECTORY = "common_directory"


class GitPlaceUnusable(StrEnum):
    """作業ディレクトリも共通の git ディレクトリも使えないときの、作業ディレクトリ側の理由。"""

    NO_WORKING_DIRECTORY = "no_working_directory"
    NOT_A_REPOSITORY = "not_a_repository"


@dataclass(frozen=True)
class GitPlace:
    path: Path
    kind: GitPlaceKind


def select_git_place(working_directory: str, git_directory: str) -> GitPlace | GitPlaceUnusable:
    """refresh が git を走らせる場所を選ぶ。どちらも使えないときは作業ディレクトリ側の理由。"""
    working = Path(working_directory)
    if not working_directory or not working.is_dir():
        unusable = GitPlaceUnusable.NO_WORKING_DIRECTORY
    elif git_succeeds(working, "rev-parse", "--git-dir"):
        return GitPlace(working, GitPlaceKind.WORKING_DIRECTORY)
    else:
        unusable = GitPlaceUnusable.NOT_A_REPOSITORY
    common = Path(git_directory)
    if git_directory and common.is_dir() and git_succeeds(common, "rev-parse", "--git-dir"):
        return GitPlace(common, GitPlaceKind.COMMON_DIRECTORY)
    return unusable


class RefreshHeadReason(StrEnum):
    """範囲のレビューの refresh が、回を作る先端をどう選んだか。"""

    # 範囲の文字列を解き直した
    RESOLVED_RANGE = "resolved_range"
    # 記録した枝の今の先端へ進めた
    FOLLOWED_BRANCH = "followed_branch"
    # 記録した枝が無いので、記録した先端の SHA で固定した
    BRANCH_MISSING = "branch_missing"
    # 追う枝を持たないので、記録した先端の SHA のままとした
    UNTRACKED = "untracked"


@dataclass(frozen=True)
class _RefreshHeadBase:
    sha: str
    # 追っている枝。追う枝を持たないときは None
    branch: str | None
    reason: ClassVar[RefreshHeadReason]

    def to_dict(self) -> dict[str, Any]:
        return {"branch": self.branch, "sha": self.sha, "reason": self.reason.value}


@dataclass(frozen=True)
class ResolvedRangeHead(_RefreshHeadBase):
    reason: ClassVar[RefreshHeadReason] = RefreshHeadReason.RESOLVED_RANGE


@dataclass(frozen=True)
class FollowedBranchHead(_RefreshHeadBase):
    branch: str
    reason: ClassVar[RefreshHeadReason] = RefreshHeadReason.FOLLOWED_BRANCH


@dataclass(frozen=True)
class BranchMissingHead(_RefreshHeadBase):
    branch: str
    reason: ClassVar[RefreshHeadReason] = RefreshHeadReason.BRANCH_MISSING


@dataclass(frozen=True)
class UntrackedHead(_RefreshHeadBase):
    branch: None = field(default=None, init=False)
    reason: ClassVar[RefreshHeadReason] = RefreshHeadReason.UNTRACKED


# 範囲のレビューの refresh が回を作った先端
RefreshHead = ResolvedRangeHead | FollowedBranchHead | BranchMissingHead | UntrackedHead


@dataclass(frozen=True)
class ResolveAgain:
    """範囲の文字列を解き直して先端を得る。branch は追っている枝。"""

    branch: str | None


@dataclass(frozen=True)
class KeepRecordedBase:
    """記録した基点のまま、head の先端で回を作る。"""

    head: FollowedBranchHead | BranchMissingHead | UntrackedHead


@dataclass(frozen=True)
class BaseLost:
    """先端が記録した基点を含まなくなった。branch は追っていた枝。"""

    head_sha: str
    branch: str | None


RefreshChoice = ResolveAgain | KeepRecordedBase | BaseLost


class GitQueries(Protocol):
    """判断の関数が git の状態を問い合わせる口。"""

    def full_reference_name(self, written: str) -> str:
        """書き方から参照の完全な名前を引く。SHA と解けない書き方は空。"""
        ...

    def branch_tip(self, name: str) -> str | None:
        """枝の先端のコミットを返す。枝が無ければ None。"""
        ...

    def is_ancestor(self, base: str, head: str) -> bool:
        """base が head の祖先（同じコミットを含む）かを返す。"""
        ...


@dataclass(frozen=True)
class GitProcessQueries:
    """git を起動して答える口。"""

    root: Path

    def full_reference_name(self, written: str) -> str:
        return _full_reference_name(self.root, written)

    def branch_tip(self, name: str) -> str | None:
        try:
            return resolve_commit(self.root, f"{_BRANCH_PREFIX}{name}")
        except ReviewError:
            return None

    def is_ancestor(self, base: str, head: str) -> bool:
        # 保存物から読んだ SHA がオプションとして解釈されないよう、--end-of-options を置く
        return git_succeeds(
            self.root, "merge-base", "--is-ancestor", "--end-of-options", base, head
        )


def select_refresh_head(
    git: GitQueries,
    place: GitPlace,
    range_text: str,
    tracking: HeadTracking,
    recorded_head_sha: str,
    merge_base: str,
) -> RefreshChoice:
    """refresh が回を作る先端の選び方を決める。

    作業ディレクトリで走らせ、先端が動く参照なら範囲の文字列を解き直す。それ以外は
    記録した基点のまま、記録した枝の先端か記録した先端の SHA を使い、基点の子孫か確かめる。
    """
    if place.kind is GitPlaceKind.WORKING_DIRECTORY and (
        (tracking is not None and tracking.kind is TrackedBranchKind.NAMED_BRANCH)
        or (tracking is None and not _head_is_fixed(git, range_text))
    ):
        return ResolveAgain(None if tracking is None else tracking.branch)
    # 共通の git ディレクトリの HEAD は作ったときの HEAD ではない。解き直さず記録の基点を使う
    head: FollowedBranchHead | BranchMissingHead | UntrackedHead
    if tracking is None:
        head = UntrackedHead(recorded_head_sha)
    else:
        tip = git.branch_tip(tracking.branch)
        if tip is None:
            head = BranchMissingHead(recorded_head_sha, tracking.branch)
        else:
            head = FollowedBranchHead(tip, tracking.branch)
    if not git.is_ancestor(merge_base, head.sha):
        return BaseLost(head.sha, None if tracking is None else tracking.branch)
    return KeepRecordedBase(head)


@dataclass(frozen=True)
class ResolvedRange:
    """範囲の文字列を解いた値。作成と refresh が同じ値を受け渡す。"""

    base_sha: str
    head_sha: str
    merge_base: str
    # 範囲の head の refs 名。既定の名前に使う
    head_name: str


def resolve_range(root: Path, range_text: str) -> ResolvedRange:
    """範囲の文字列を base、head、共通の祖先のコミットに解く。

    refs が解けない、共通の祖先が無い、範囲に変更が無いときは ReviewError。
    """
    refs = split_range(range_text)
    base_sha = resolve_commit(root, refs.base)
    head_sha = resolve_commit(root, refs.head)
    try:
        base = parse_commit_sha("base", base_sha)
        head = parse_commit_sha("head", head_sha)
        merge_base = process.run_command(
            build_git_command(GitSubcommand.MERGE_BASE, revisions=[base, head]), cwd=root
        ).strip()
    except process.CommandError as error:
        raise ReviewError(f"共通の祖先がありません: {range_text}") from error
    changed = process.run_command(
        build_git_command(
            GitSubcommand.DIFF,
            options=["--name-only"],
            revisions=[parse_commit_sha("merge-base", merge_base), head],
        ),
        cwd=root,
    )
    if not changed.strip():
        raise ReviewError(f"範囲に変更がありません: {range_text}")
    return ResolvedRange(base_sha, head_sha, merge_base, refs.head)


@dataclass(frozen=True)
class NewRangePlace:
    """範囲のレビューを作るときに記録する、共通の git ディレクトリと先端の追い方。"""

    git_directory: str
    head_tracking: HeadTracking


def new_range_place(root: Path, range_text: str, head_sha: str) -> NewRangePlace:
    common = run_git(root, "rev-parse", "--git-common-dir").strip()
    # git は作業ディレクトリからの相対で返すことがある。プロセスの現在地でなく root から解く
    git_directory = (root / common).resolve()
    return NewRangePlace(str(git_directory), head_tracking_of(root, range_text, head_sha))


@dataclass(frozen=True)
class RefreshedRange:
    """refresh が回を作る範囲と、その先端の選び方。"""

    resolved: ResolvedRange
    head: RefreshHead


def plan_refresh_range(
    git: GitQueries,
    place: GitPlace,
    range_text: str,
    tracking: HeadTracking,
    recorded_head_sha: str,
    recorded_base_sha: str,
    merge_base: str,
) -> RefreshedRange | BaseLost:
    """refresh が回を作る範囲を決める。基点を失ったときは BaseLost。"""
    choice = select_refresh_head(git, place, range_text, tracking, recorded_head_sha, merge_base)
    match choice:
        case ResolveAgain():
            resolved = resolve_range(place.path, range_text)
            return RefreshedRange(resolved, ResolvedRangeHead(resolved.head_sha, choice.branch))
        case KeepRecordedBase():
            head = choice.head
            resolved = ResolvedRange(
                recorded_base_sha, head.sha, merge_base, split_range(range_text).head
            )
            return RefreshedRange(resolved, head)
        case BaseLost():
            return choice
        case _:
            assert_never(choice)
