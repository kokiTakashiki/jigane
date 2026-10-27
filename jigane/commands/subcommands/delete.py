"""`jigane delete`: レビューのディレクトリをデータの置き場から消す。"""

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.reviews.lifecycle import REVIEW_FILE, TRASH_DIRECTORY, resolve_review
from jigane.storage.store import LOCK_FILE, ReviewId, data_home, review_lock


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    deleting = subparsers.add_parser("delete", help="レビューを消します。戻せません")
    deleting.add_argument("review")
    deleting.add_argument("--yes", action="store_true", help="確認を省きます")
    deleting.add_argument("--json", action="store_true")
    deleting.set_defaults(handler=run)


def remove_review_directory(review_id: ReviewId) -> None:
    # directory() が、置き場の外を指す経路（シンボリックリンク）を拒む
    directory = review_id.directory()
    home = data_home().resolve()
    if directory.resolve() == home:
        raise ValueError(f"データの置き場そのものは消せません: {directory}")
    tombstone = home / TRASH_DIRECTORY / uuid.uuid4().hex
    with review_lock(directory):
        # 開いたままのロックのファイルは Windows では消せない。残し、ロックを離してから消す。
        # 他の中身は、同じファイルシステム上の墓標へ 1 件ずつ改名して退ける。改名は 1 件ごとに
        # 原子的なので、途中で失敗しても元へ戻せる。レビューの本体（review.json）を先に動かす
        move_entries_aside(directory, tombstone)
    # 離してから消すまでに握った待ち手は、状態が無く既存の ReviewError で失敗する。それでよい
    shutil.rmtree(directory)
    # 墓標は消す前に落ちても、レビューの一覧に出ないので残っても害が無い
    shutil.rmtree(tombstone, ignore_errors=True)
    remove_empty_parents(directory, home)


def move_entries_aside(directory: Path, tombstone: Path) -> None:
    """ロックのファイルを除く中身を墓標へ移す。失敗したら移した分を戻して例外を上げる。"""
    entries = sorted(
        (entry for entry in directory.iterdir() if entry.name != LOCK_FILE),
        key=lambda entry: entry.name != REVIEW_FILE,
    )
    tombstone.mkdir(parents=True)
    moved: list[Path] = []
    try:
        for entry in entries:
            entry.rename(tombstone / entry.name)
            moved.append(entry)
    except OSError as error:
        stuck: list[str] = []
        for entry in reversed(moved):
            try:
                (tombstone / entry.name).rename(entry)
            except OSError as rollback_error:
                # 戻せない分で止めると元の失敗が隠れるため、残りも戻し続けて注記に集める
                stuck.append(f"{entry.name}: {rollback_error}")
        if stuck:
            error.add_note(
                f"移した中身を戻せませんでした。墓標 {tombstone} に残っています: "
                + "; ".join(stuck)
            )
        else:
            tombstone.rmdir()
        raise


def remove_empty_parents(directory: Path, home: Path) -> None:
    parent = directory.parent
    # 置き場そのものは残す。他のレビューが入っている親は rmdir が失敗するので、そこで止める
    while parent.resolve() != home and parent.resolve().is_relative_to(home):
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def confirm_deletion(review_id: ReviewId) -> None:
    if not sys.stdin.isatty():
        raise ValueError("確認できません。--yes を付けてください")
    answer = input(f"{review_id} を削除します。戻せません。よろしいですか [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        raise ValueError("削除を取りやめました")


def run(args: argparse.Namespace, context: Context) -> int:
    del context  # 消すだけで LLM も外部のコマンドも呼ばない
    review_id = resolve_review(args.review)
    if not args.yes:
        confirm_deletion(review_id)
    remove_review_directory(review_id)
    if args.json:
        print(json.dumps({"deleted": str(review_id)}, ensure_ascii=False))
    else:
        print(f"削除しました: {review_id}")
    return EXIT_OK
