"""レビューボットのインラインのコメントと、指摘への対応の判定。"""

import difflib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from jigane.git import process
from jigane.git.command_line import GitSubcommand, build_git_command
from jigane.git.revision import CommitSha, parse_commit_sha
from jigane.git.tree_files import read_tree_text
from jigane.judgment_map.classification import BotTouch
from jigane.preconditions.required import required
from jigane.pull_request_context import gh
from jigane.storage.review_error import ReviewError
from jigane.storage.store_format import require_current_version
from jigane.text.json_input import loads_bounded

BOT_COMMENTS_FILE = "bot_comments.json"
# bot_comments.json の形の版。保存物の形を変えたら一つ上げる
BOT_COMMENTS_FORMAT = 3
BODY_CHAR_LIMIT = 2000
# 統一形式の差分の文脈の行数。GitHub がコメントと一緒に見せる範囲でもある
CONTEXT_LINES = 3
# 判定から外したコメントの警告の先頭の文言。refresh が引き継ぐために使う
UNAVAILABLE_WARNING_PREFIX = "ボットのコメント"


@dataclass(frozen=True)
class BotComment:
    id: int
    user: str
    body: str
    path: str
    original_commit_id: str
    original_line: int
    original_start_line: int | None
    html_url: str


@dataclass(frozen=True)
class BotJudgment:
    comment: BotComment
    # 付いたコミットか head のファイルが取れず、判定できなかったとき False
    judged: bool
    # コメントの後の変更で触れた head の行
    touched_lines: tuple[int, ...]


@dataclass(frozen=True)
class BotAnalysis:
    judgments: tuple[BotJudgment, ...] = ()

    @property
    def unavailable_count(self) -> int:
        return sum(1 for j in self.judgments if not j.judged)

    def touches(self) -> list[BotTouch]:
        return [
            BotTouch(j.comment.id, j.comment.path, frozenset(j.touched_lines))
            for j in self.judgments
            if j.touched_lines
        ]

    def warnings(self) -> list[str]:
        if not self.unavailable_count:
            return []
        return [
            (
                f"{UNAVAILABLE_WARNING_PREFIX} {self.unavailable_count} 件は、"
                "付いたコミットまたは head のファイルを取れず、判定から外しました"
            )
        ]

    def to_dict(self, region_keys: dict[int, list[str]] | None = None) -> dict[str, Any]:
        keys = region_keys or {}
        return {
            "comments": [
                {
                    **asdict(j.comment),
                    "judged": j.judged,
                    "touched_lines": list(j.touched_lines),
                    "region_keys": keys.get(j.comment.id, []),
                }
                for j in self.judgments
            ]
        }


@dataclass(frozen=True)
class StoredBotJudgment:
    """保存から戻した判定。region_keys は、そのコメントに関わる判断の箇所の鍵。"""

    judgment: BotJudgment
    region_keys: tuple[str, ...]


@dataclass(frozen=True)
class StoredBotComments:
    judgments: tuple[StoredBotJudgment, ...]

    @property
    def unavailable_count(self) -> int:
        return sum(1 for item in self.judgments if not item.judgment.judged)

    def analysis(self) -> BotAnalysis:
        return BotAnalysis(tuple(item.judgment for item in self.judgments))


def dump_bot_comments(
    analysis: BotAnalysis, region_keys: dict[int, list[str]] | None = None
) -> dict[str, Any]:
    """bot_comments.json へ書く境の一か所。最上位に今の版を刻む。"""
    return {"format": BOT_COMMENTS_FORMAT, **analysis.to_dict(region_keys)}


def load_bot_comments(raw: Mapping[str, Any]) -> StoredBotComments:
    """bot_comments.json を読む境の一か所。今の版以外は止める。"""
    stored = require_current_version(BOT_COMMENTS_FILE, raw, current=BOT_COMMENTS_FORMAT)
    try:
        judgments = tuple(
            StoredBotJudgment(
                BotJudgment(
                    BotComment(**{name: item[name] for name in BotComment.__dataclass_fields__}),
                    item["judged"],
                    tuple(item["touched_lines"]),
                ),
                tuple(item["region_keys"]),
            )
            for item in stored["comments"]
        )
    except (KeyError, TypeError) as error:
        raise ReviewError(f"{BOT_COMMENTS_FILE} の中身が読めません: {error!r}") from error
    return StoredBotComments(judgments)


def read_bot_comments(directory: Path) -> StoredBotComments | None:
    """置き場の bot_comments.json を読む。ボットのコメントを保存する前のレビューは None。

    壊れた保存は ReviewError で止める。None に丸めると、保存が無いレビューと見分けが付かない。
    """
    path = directory / BOT_COMMENTS_FILE
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReviewError(f"{BOT_COMMENTS_FILE} が読めません: {error!r}") from error
    if not isinstance(raw, dict):
        raise ReviewError(f"{BOT_COMMENTS_FILE} の最上位が辞書ではありません")
    return load_bot_comments(raw)


def _is_bot(comment: dict[str, Any], bots: tuple[str, ...]) -> bool:
    user = comment.get("user") or {}
    return user.get("type") == "Bot" or user.get("login") in bots


def fetch_bot_comments(
    owner: str, repo: str, number: int, bots: tuple[str, ...]
) -> list[BotComment]:
    output = gh.pull_request_comments(owner, repo, number)
    try:
        pages = cast("list[list[dict[str, Any]]]", loads_bounded(output, gh.MAX_RESPONSE_NESTING))
    except ValueError:
        # 出力の中身は端末へ出さない。巨大な値や制御文字が混じりうる
        raise ReviewError(
            "gh api（PR のコメントの取得）の出力を JSON として読めませんでした。"
            "構文に誤りがあるか、入れ子が深すぎます。"
            "gh を新しいバージョンに更新してから、もう一度実行してください。"
        ) from None
    # --slurp はページごとの配列を、さらに一つの配列に包む
    raw = [comment for page in pages for comment in page]
    found: list[BotComment] = []
    for comment in raw:
        if not _is_bot(comment, bots):
            continue
        # 返信は元の指摘と同じ位置を指し、ファイル単位のコメントは行を持たない。
        # どちらも「コメントの付いた行」を言えないため、判定に使わない
        if comment.get("in_reply_to_id") is not None:
            continue
        if comment.get("subject_type") == "file":
            continue
        if comment.get("original_line") is None:
            continue
        found.append(
            BotComment(
                id=comment["id"],
                user=comment["user"]["login"],
                body=comment["body"][:BODY_CHAR_LIMIT],
                path=comment["path"],
                original_commit_id=comment["original_commit_id"],
                original_line=comment["original_line"],
                original_start_line=comment.get("original_start_line"),
                html_url=comment["html_url"],
            )
        )
    return found


def _split_lines(text: str) -> list[str]:
    # run_command は改行を正規化して返すため、head の側も合わせる
    lines = text.replace("\r\n", "\n").split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _touched_lines(
    old_lines: list[str], new_lines: list[str], first: int, last: int
) -> tuple[int, ...]:
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    touched: set[int] = set()
    low, high = first - CONTEXT_LINES, last + CONTEXT_LINES
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        # 変更前の範囲（1 始まり i1+1..i2）が窓に重なるか、純粋な挿入（i1 == i2、
        # i1 行目の後に入る）の位置が窓に入るハンクを拾う
        hit = low <= i1 <= high if i1 == i2 else i1 + 1 <= high and low <= i2
        if hit:
            touched.update(range(j1 + 1, j2 + 1))
    return tuple(sorted(touched))


def analyze(git_dir: Path, head_tree: Path, comments: list[BotComment]) -> BotAnalysis:
    # 取れたコミットは検査済みの CommitSha、取れなかったものは None
    fetched: dict[str, CommitSha | None] = {}
    judgments: list[BotJudgment] = []
    for comment in comments:
        sha = comment.original_commit_id
        if sha not in fetched:
            try:
                # GitHub の応答の値は形の保証が無い。SHA でなければ、取れないコミットと同じに扱う
                commit = parse_commit_sha("original_commit_id", sha)
                process.run_command(
                    build_git_command(
                        GitSubcommand.FETCH, fixed_values=["origin"], revisions=[commit]
                    ),
                    cwd=git_dir,
                )
                fetched[sha] = commit
            except (process.CommandError, ValueError):
                # force-push の後などで、コミットがもう取れない
                fetched[sha] = None
        head_text = read_tree_text(head_tree, comment.path)
        old_text: str | None = None
        fetched_commit = fetched[sha]
        if fetched_commit is not None and head_text is not None:
            try:
                old_text = process.run_command(
                    build_git_command(
                        GitSubcommand.SHOW,
                        revision_paths=[(fetched_commit, comment.path)],
                    ),
                    cwd=git_dir,
                )
            except (process.CommandError, UnicodeDecodeError):
                old_text = None
        if old_text is None:
            judgments.append(BotJudgment(comment, judged=False, touched_lines=()))
            continue
        first = comment.original_start_line or comment.original_line
        touched = _touched_lines(
            _split_lines(old_text),
            _split_lines(required(head_text)),
            first,
            comment.original_line,
        )
        judgments.append(BotJudgment(comment, judged=True, touched_lines=touched))
    return BotAnalysis(tuple(judgments))


def collect(
    owner: str,
    repo: str,
    number: int,
    git_dir: Path,
    head_tree: Path,
    *,
    bots: tuple[str, ...],
) -> BotAnalysis:
    return analyze(git_dir, head_tree, fetch_bot_comments(owner, repo, number, bots))
