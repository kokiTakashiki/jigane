"""JIGANE_HOME の repository-docs.toml を読み、リポジトリごとの文書のパスを引く。"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from jigane.repository_docs.kinds import DOCUMENT_KINDS, DocumentKind
from jigane.storage.store import COMPONENT_PATTERN, data_home

SETTINGS_FILE = "repository-docs.toml"
REPOSITORIES_TABLE = "repositories"
_REPOSITORY_NAME = re.compile(f"{COMPONENT_PATTERN.pattern}/{COMPONENT_PATTERN.pattern}")


class RepositoryDocsError(Exception):
    """設定ファイルそのものの誤り。どの文書を指したのかを知れないので、レビューを止める。"""


@dataclass(frozen=True)
class ConfiguredPath:
    """設定の値と、警告に出す出どころ（パスは出さない）。"""

    value: str
    origin: str


@dataclass(frozen=True)
class RepositoryDocsSettings:
    defaults: dict[DocumentKind, str]
    repositories: dict[str, dict[DocumentKind, str]]

    def resolve(self, owner: str, repo: str) -> dict[DocumentKind, ConfiguredPath]:
        """指定のある種類だけを返す。リポジトリの表、全体の既定の順に、キーの単位で引く。"""
        name = f"{owner}/{repo}"
        table = self.repositories.get(name.casefold(), {})
        found: dict[DocumentKind, ConfiguredPath] = {}
        for spec in DOCUMENT_KINDS:
            if table.get(spec.kind):
                found[spec.kind] = ConfiguredPath(
                    table[spec.kind], f'[{REPOSITORIES_TABLE}."{name}"] の {spec.config_key}'
                )
            elif self.defaults.get(spec.kind):
                found[spec.kind] = ConfiguredPath(
                    self.defaults[spec.kind], f"最上位の {spec.config_key}"
                )
            # どちらも無いか空文字列なら、指定の無い種類として結果に入れない
        return found


def _read_keys(table: dict[str, object], where: str) -> dict[DocumentKind, str]:
    keys = {spec.config_key: spec.kind for spec in DOCUMENT_KINDS}
    values: dict[DocumentKind, str] = {}
    for key, value in table.items():
        if key not in keys:
            raise RepositoryDocsError(
                f"{SETTINGS_FILE} の {where} に知らないキー {key} があります"
            )
        if not isinstance(value, str):
            raise RepositoryDocsError(
                f"{SETTINGS_FILE} の {where} の {key} は文字列にしてください"
            )
        values[keys[key]] = value
    return values


def parse_settings(text: str) -> RepositoryDocsSettings:
    try:
        document = tomllib.loads(text)
    # 深い入れ子は tomllib が RecursionError で拒む。TOML として読めないものと同じに扱う
    except (tomllib.TOMLDecodeError, RecursionError) as error:
        raise RepositoryDocsError(f"{SETTINGS_FILE} を TOML として読めません: {error}") from error
    tables = document.pop(REPOSITORIES_TABLE, {})
    if not isinstance(tables, dict):
        raise RepositoryDocsError(f"{SETTINGS_FILE} の {REPOSITORIES_TABLE} は表にしてください")
    repositories: dict[str, dict[DocumentKind, str]] = {}
    for name, table in tables.items():
        if _REPOSITORY_NAME.fullmatch(name) is None:
            raise RepositoryDocsError(
                f"{SETTINGS_FILE} の表の名前 {name} は owner/repo の形にしてください"
            )
        if not isinstance(table, dict):
            raise RepositoryDocsError(f"{SETTINGS_FILE} の {name} は表にしてください")
        if name.casefold() in repositories:
            raise RepositoryDocsError(
                f"{SETTINGS_FILE} に大文字小文字だけが違う表があります: {name}"
            )
        repositories[name.casefold()] = _read_keys(table, f'[{REPOSITORIES_TABLE}."{name}"]')
    return RepositoryDocsSettings(_read_keys(document, "最上位"), repositories)


def load_settings() -> RepositoryDocsSettings:
    """データの置き場の設定を読む。ファイルが無ければ、何も指定しない設定を返す。"""
    path = data_home() / SETTINGS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return RepositoryDocsSettings({}, {})
    except (OSError, UnicodeDecodeError) as error:
        raise RepositoryDocsError(f"{SETTINGS_FILE} を読めません: {error}") from error
    return parse_settings(text)


def settings_path() -> Path:
    return data_home() / SETTINGS_FILE
