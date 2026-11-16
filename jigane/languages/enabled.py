import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from jigane.languages.approval import (
    ApprovedContent,
    FileDigest,
    is_sha256_hex,
)
from jigane.storage.atomic_write import write_text_atomic
from jigane.text.distribution_names import normalize_distribution
from jigane.text.json_input import (
    SETTINGS_NESTING_LIMIT,
    JsonTooDeepError,
    loads_bounded,
    settings_too_deep_message,
)

ENABLED_FILE = "languages.json"


class EnabledFileError(Exception):
    """languages.json が読めない、JSON でない、または形が違う。"""


class OlderRecordError(EnabledFileError):
    """languages.json の要素が、中身を記録する前の古い形式。消して承認し直す手順を文に含む。"""

    def __init__(self, path: Path, name: str) -> None:
        super().__init__(
            f"{path} の「{name}」は古い形式です。この languages.json を消してから、"
            f"`jigane languages enable {name}` で承認し直してください"
            "（有効にした言語は、すべて承認し直しになります）。"
        )


class _OlderFormError(ValueError):
    """from_json が files の無い要素を見つけた印。置き場を知る load_enabled が文にする。"""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


@dataclass(frozen=True)
class EnabledLanguage:
    """利用者が有効にした言語。言語の名前、提供する配布の名前、承認した配布の版、承認の中身で記録する。"""

    name: str
    distribution: str
    version: str
    content: ApprovedContent

    def __post_init__(self) -> None:
        # 正規化した形で持てば、等値と重複の除去が表記の揺れに左右されない
        object.__setattr__(self, "distribution", normalize_distribution(self.distribution))

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "distribution": self.distribution,
            "version": self.version,
            "files": {file.path: file.sha256 for file in self.content.files},
        }

    @classmethod
    def from_json(cls, item: object) -> "EnabledLanguage":
        """languages.json の一件から復元する。形違いは ValueError。"""
        keys = {"name", "distribution", "version"}
        if isinstance(item, dict) and set(item) == keys and isinstance(item["name"], str):
            raise _OlderFormError(item["name"])
        if not isinstance(item, dict) or set(item) != keys | {"files"}:
            raise ValueError(
                "enabled の要素は name と distribution と version と files だけを持ちます"
            )
        name, distribution, version = item["name"], item["distribution"], item["version"]
        if not all(isinstance(value, str) for value in (name, distribution, version)):
            raise ValueError("name と distribution と version は文字列でなければなりません")
        files = item["files"]
        if not isinstance(files, dict) or not all(
            isinstance(path, str) and isinstance(digest, str) and is_sha256_hex(digest)
            for path, digest in files.items()
        ):
            raise ValueError("files は相対パスから sha256 の 16 進への対応でなければなりません")
        return cls(
            name,
            distribution,
            version,
            ApprovedContent(
                tuple(FileDigest(path, digest) for path, digest in sorted(files.items()))
            ),
        )


def load_enabled(path: Path) -> tuple[EnabledLanguage, ...]:
    if not path.exists():
        # 一度も有効にしていない利用者の通常の状態であり、誤りではない
        return ()
    try:
        data = loads_bounded(path.read_text(encoding="utf-8"), SETTINGS_NESTING_LIMIT)
    except (OSError, UnicodeDecodeError) as error:
        raise EnabledFileError(f"{path} を読めません: {error}") from error
    except JsonTooDeepError as error:
        raise EnabledFileError(settings_too_deep_message(path, error)) from error
    except json.JSONDecodeError as error:
        raise EnabledFileError(f"{path} は JSON ではありません: {error}") from error
    shape_error = EnabledFileError(
        f"{path} の形が違います。"
        f'{{"enabled": [{{"name": ..., "distribution": ..., "version": ..., "files": {{...}}}}]}} '
        "だけを書きます"
    )
    if (
        not isinstance(data, dict)
        or set(data) != {"enabled"}
        or not isinstance(data["enabled"], list)
    ):
        raise shape_error
    entries: dict[EnabledLanguage, None] = {}
    for item in data["enabled"]:
        try:
            entry = EnabledLanguage.from_json(item)
        except _OlderFormError as error:
            raise OlderRecordError(path.resolve(), error.name) from error
        except ValueError as error:
            raise shape_error from error
        # 同じ組が重なっても誤りにしない。順序を保って一つにまとめる
        entries[entry] = None
    return tuple(entries)


def save_enabled(path: Path, enabled: Iterable[EnabledLanguage]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [e.to_json() for e in dict.fromkeys(enabled)]
    body = json.dumps({"enabled": entries}, ensure_ascii=False, indent=2)
    write_text_atomic(path, body + "\n")
