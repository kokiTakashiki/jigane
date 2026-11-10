import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from jigane.languages.approval import (
    Approval,
    ApprovedContent,
    FileDigest,
    LegacyApproval,
    is_sha256_hex,
)
from jigane.text.distribution_names import normalize_distribution

ENABLED_FILE = "languages.json"


class EnabledFileError(Exception):
    """languages.json が読めない、JSON でない、または形が違う。"""


@dataclass(frozen=True)
class EnabledLanguage:
    """利用者が有効にした言語。言語の名前、提供する配布の名前、承認した配布の版、承認の中身で記録する。"""

    name: str
    distribution: str
    version: str
    content: Approval

    def __post_init__(self) -> None:
        # 正規化した形で持てば、等値と重複の除去が表記の揺れに左右されない
        object.__setattr__(self, "distribution", normalize_distribution(self.distribution))

    def to_json(self) -> dict[str, object]:
        body: dict[str, object] = {
            "name": self.name,
            "distribution": self.distribution,
            "version": self.version,
        }
        if isinstance(self.content, ApprovedContent):
            body["files"] = {file.path: file.sha256 for file in self.content.files}
        return body

    @classmethod
    def from_json(cls, item: object) -> "EnabledLanguage":
        """languages.json の一件から復元する。形が違えば ValueError。"""
        legacy_keys = {"name", "distribution", "version"}
        if not isinstance(item, dict) or set(item) not in (legacy_keys, legacy_keys | {"files"}):
            raise ValueError(
                "enabled の要素は name と distribution と version と files だけを持ちます"
            )
        name, distribution, version = item["name"], item["distribution"], item["version"]
        if not all(isinstance(value, str) for value in (name, distribution, version)):
            raise ValueError("name と distribution と version は文字列でなければなりません")
        if "files" not in item:
            # 中身を記録する前の形。読めるが、承認としては使わない
            return cls(name, distribution, version, LegacyApproval())
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
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise EnabledFileError(f"{path} を読めません: {error}") from error
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
        except ValueError as error:
            raise shape_error from error
        # 同じ組が重なっても誤りにしない。順序を保って一つにまとめる
        entries[entry] = None
    return tuple(entries)


def save_enabled(path: Path, enabled: Iterable[EnabledLanguage]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [e.to_json() for e in dict.fromkeys(enabled)]
    body = json.dumps({"enabled": entries}, ensure_ascii=False, indent=2)
    path.write_text(body + "\n", encoding="utf-8")
