import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import assert_never

from jigane.git.tree_files import (
    MAX_REVIEW_FILE_BYTES,
    Content,
    NotOpened,
    TooLarge,
    TreeFileState,
    read_tree,
)
from jigane.storage.review_error import ReviewError

CONFIG_FILE_NAME = ".jigane.toml"

DEFAULT_EXCLUDE = (
    "*.lock",
    "package-lock.json",
    "*/package-lock.json",
    "*.min.js",
    "*.min.css",
    "vendor/*",
    "*/vendor/*",
    "packages.lock.json",
    "*/packages.lock.json",
    "*.Designer.cs",
    "*.g.cs",
    "gradle.lockfile",
    "*/gradle.lockfile",
    "Package.resolved",
    "*/Package.resolved",
    "Podfile.lock",
    "*/Podfile.lock",
)


@dataclass(frozen=True)
class Config:
    # リポジトリのルートからの posix の相対パス全体に fnmatchcase で当てる glob
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE
    sensitive: tuple[str, ...] = ()
    # `user.type` が Bot のアカウントは、ここに無くても常にボットとして扱う
    bots: tuple[str, ...] = ()


def _globs(table: dict[str, object], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    if key not in table:
        return default
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ReviewError(
            f"レビューする木の {CONFIG_FILE_NAME} の {key} の値が正しくありません。"
            f'{key} は文字列の配列（例: {key} = ["gen/*"]）にしてください。'
            f"{CONFIG_FILE_NAME} を直してから、もう一度実行してください。"
        )
    return tuple(value)


def _config_for_unopened(state: TreeFileState, detail: str) -> Config:
    match state:
        case TreeFileState.MISSING:
            return Config()
        case TreeFileState.UNREADABLE:
            raise ReviewError(
                f"レビューする木の {CONFIG_FILE_NAME} を開けませんでした。"
                "詳細に出ている原因（権限、ファイルの種類など）を取り除いてから、"
                "もう一度実行してください。"
                f"（詳細: {detail}）"
            )
        case TreeFileState.REFUSED:
            raise ReviewError(
                f"レビューする木の {CONFIG_FILE_NAME} は、"
                "シンボリックリンクか、木の外を指すか、"
                "確かめた後に差し替わったので読みません。"
                "既定に落とすと設定した除外や機微の指定が効かないため止めます。"
                "通常のファイルに置き換えてから、もう一度実行してください。"
            )
        case TreeFileState.NOT_REGULAR:
            raise ReviewError(
                f"レビューする木の {CONFIG_FILE_NAME} は"
                "通常のファイルではないので読みません。"
                "既定に落とすと設定した除外や機微の指定が効かないため止めます。"
                "通常のファイルに置き換えてから、もう一度実行してください。"
            )
        case TreeFileState.READABLE:
            # 読めたファイルは Content で返るので、READABLE の NotOpened は作られない。
            # 型の上では残るので、網羅のために塞ぐ
            raise AssertionError("READABLE の NotOpened は作られない")
        case _ as unreachable:
            assert_never(unreachable)


def load_config(repository_root: Path) -> Config:
    outcome = read_tree(repository_root, CONFIG_FILE_NAME, MAX_REVIEW_FILE_BYTES)
    match outcome:
        case Content(data=data):
            pass
        case TooLarge():
            # 黙って既定に落とすと、設定した除外や機微の指定が効かないまま進む。止める
            raise ReviewError(
                f"レビューする木の {CONFIG_FILE_NAME} が大きすぎます"
                f"（上限 {MAX_REVIEW_FILE_BYTES} バイト）。"
                "小さくしてから、もう一度実行してください。"
            )
        case NotOpened(state=state, detail=detail):
            return _config_for_unopened(state, detail)
        case _ as unreachable:
            assert_never(unreachable)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ReviewError(
            f"レビューする木の {CONFIG_FILE_NAME} を読めませんでした。UTF-8 として読めません。"
            f"{CONFIG_FILE_NAME} を UTF-8 で保存し直してから、もう一度実行してください。"
            f"（詳細: {error}）"
        ) from error
    try:
        table = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError) as error:
        detail = str(error) or type(error).__name__
        raise ReviewError(
            f"レビューする木の {CONFIG_FILE_NAME} を読めませんでした。"
            "TOML の書き方に誤りがあるか、入れ子が深すぎます。"
            f"{CONFIG_FILE_NAME} を直してから、もう一度実行してください。（詳細: {detail}）"
        ) from error
    defaults = Config()
    return Config(
        exclude=_globs(table, "exclude", defaults.exclude),
        sensitive=_globs(table, "sensitive", defaults.sensitive),
        bots=_globs(table, "bots", defaults.bots),
    )


def matches_any(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def is_excluded(path: str, config: Config) -> bool:
    """path が config.exclude に合い、差分から外れる生成物なら True を返す。"""
    return matches_any(path, config.exclude)
