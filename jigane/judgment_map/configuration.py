import tomllib
from dataclasses import dataclass
from pathlib import Path

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
        raise ValueError(f"{CONFIG_FILE_NAME}: {key} must be an array of strings")
    return tuple(value)


def load_config(repository_root: Path) -> Config:
    path = repository_root / CONFIG_FILE_NAME
    if not path.is_file():
        return Config()
    table = tomllib.loads(path.read_text(encoding="utf-8"))
    defaults = Config()
    return Config(
        exclude=_globs(table, "exclude", defaults.exclude),
        sensitive=_globs(table, "sensitive", defaults.sensitive),
        bots=_globs(table, "bots", defaults.bots),
    )
