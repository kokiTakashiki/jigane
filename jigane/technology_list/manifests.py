"""マニフェストを文字列として一度だけ読み、依存の宣言と配備の下限を取り出す。

木を一度走査し、ファイル名で選んだ抽出器に読んだ文字列を渡す。抽出器は二種類で、
依存の宣言（パッケージの名前と版）と配備の下限（最低対応 OS や実行環境の版）である。
マニフェストは評価も実行もしない。`Package.swift` も正規表現で読む。
配備の下限は、変更したファイルに効くマニフェストだけを対象にする。
"""

import json
import re
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

from jigane.git.tree_files import read_tree_text, walk_tree_files
from jigane.technology_list.changes import UNCHANGED, change_of

PACKAGE = "package"
PRODUCT = "product"

DOTNET = ".NET"
PYTHON = "Python"

CENTRAL_FILE = "Directory.Packages.props"


@dataclass(frozen=True)
class Dependency:
    """宣言の一件。path は root からの posix の相対パス、line は 1 始まり。"""

    language: str
    name: str
    version: str | None
    path: str
    line: int
    # PACKAGE は取り込む単位、PRODUCT は Swift の `.product` が名指す、パッケージの中の名前
    kind: str = PACKAGE


_SWIFT_PLATFORM = re.compile(
    r'\.(iOS|macOS|tvOS|watchOS|visionOS|macCatalyst)\s*\(\s*(?:\.v([0-9_]+)|"([^"]+)")'
)
_PBXPROJ_TARGET = re.compile(
    r"\b(IPHONEOS|MACOSX|TVOS|WATCHOS|XROS)_DEPLOYMENT_TARGET\s*=\s*([^;\s]+)\s*;"
)
_PBXPROJ_NAME = {
    "IPHONEOS": "iOS",
    "MACOSX": "macOS",
    "TVOS": "tvOS",
    "WATCHOS": "watchOS",
    "XROS": "visionOS",
}
_TARGET_FRAMEWORKS = re.compile(r"<TargetFrameworks?>\s*([^<]*?)\s*</TargetFrameworks?>")
_SWIFT_PACKAGE = re.compile(r"\.package\s*\(((?:[^()]|\([^()]*\))*)\)")
_SWIFT_URL = re.compile(r'(?:url|path):\s*"([^"]+)"')
_SWIFT_VERSION = re.compile(r'(?:from|exact|branch|revision):\s*"([^"]+)"|"(\d[^"]*)"\s*\.\.[.<]')
_SWIFT_PRODUCT = re.compile(r'\.product\s*\(\s*name:\s*"([^"]+)"\s*,\s*package:\s*"([^"]+)"')
_CSPROJ_TAG = re.compile(r"<(PackageReference|PackageVersion)\b([^>]*?)/?>", re.DOTALL)
_XML_ATTRIBUTE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_GRADLE = re.compile(
    r"\b(?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly"
    r"|testCompileOnly|annotationProcessor|kapt|ksp|classpath|developmentOnly)"
    r"""\s*\(?\s*["']([\w.\-]+):([\w.\-]+)(?::([^"':]+))?[^"']*["']"""
)
_POM_DEPENDENCY = re.compile(r"<dependency>(.*?)</dependency>", re.DOTALL)
_PYTHON_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([^;]*)")


@dataclass(frozen=True)
class Platform:
    """配備の下限の一件。path は root からの posix の相対パス。"""

    path: str
    platform: str
    minimum: str


def normalize_python(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _swift(text: str, path: str) -> list[Dependency]:
    found: list[Dependency] = []
    for match in _SWIFT_PACKAGE.finditer(text):
        body = match.group(1)
        location = _SWIFT_URL.search(body)
        if location is None:
            continue
        name = location.group(1).rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        version = _SWIFT_VERSION.search(body)
        found.append(
            Dependency(
                "swift",
                name,
                None if version is None else version.group(1) or version.group(2),
                path,
                _line_of(text, match.start()),
            )
        )
    found.extend(
        Dependency("swift", m.group(1), None, path, _line_of(text, m.start()), PRODUCT)
        for m in _SWIFT_PRODUCT.finditer(text)
    )
    return found


def _csharp(text: str, path: str) -> list[Dependency]:
    found: list[Dependency] = []
    for match in _CSPROJ_TAG.finditer(text):
        attributes = dict(_XML_ATTRIBUTE.findall(match.group(2)))
        name = attributes.get("Include") or attributes.get("Update")
        if name is None:
            continue
        found.append(
            Dependency(
                "csharp",
                name,
                attributes.get("Version") or attributes.get("VersionOverride"),
                path,
                _line_of(text, match.start()),
            )
        )
    return found


def _toml(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        # 壊れた宣言は読めないので、宣言も下限も無いものとして扱う
        return {}


def _python(text: str, path: str) -> list[Dependency]:
    data = _toml(text)
    project = data.get("project", {})
    requirements: list[str] = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        requirements.extend(group)
    for group in data.get("dependency-groups", {}).values():
        # `{include-group = "x"}` は別の群への参照なので、文字列だけを宣言とする
        requirements.extend(item for item in group if isinstance(item, str))
    lines = text.splitlines()
    found: list[Dependency] = []
    for requirement in requirements:
        parsed = _PYTHON_REQUIREMENT.match(requirement)
        if parsed is None:
            continue
        line = next((i for i, row in enumerate(lines, 1) if requirement in row), 1)
        found.append(
            Dependency("python", parsed.group(1), parsed.group(2).strip() or None, path, line)
        )
    return found


def _javascript(text: str, path: str) -> list[Dependency]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    lines = text.splitlines()
    found: list[Dependency] = []
    for section in ("dependencies", "devDependencies"):
        entries = data.get(section)
        if not isinstance(entries, dict):
            continue
        for name, version in entries.items():
            line = next((i for i, row in enumerate(lines, 1) if f'"{name}"' in row), 1)
            found.append(
                Dependency(
                    "javascript", name, version if isinstance(version, str) else None, path, line
                )
            )
    return found


def _gradle(text: str, path: str) -> list[Dependency]:
    return [
        Dependency(
            "java", f"{m.group(1)}:{m.group(2)}", m.group(3), path, _line_of(text, m.start())
        )
        for m in _GRADLE.finditer(text)
    ]


def _pom(text: str, path: str) -> list[Dependency]:
    found: list[Dependency] = []
    for match in _POM_DEPENDENCY.finditer(text):
        body = match.group(1)
        group = re.search(r"<groupId>\s*([^<\s]+)\s*</groupId>", body)
        artifact = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", body)
        version = re.search(r"<version>\s*([^<\s]+)\s*</version>", body)
        if group is None or artifact is None:
            continue
        found.append(
            Dependency(
                "java",
                f"{group.group(1)}:{artifact.group(1)}",
                None if version is None else version.group(1),
                path,
                _line_of(text, match.start()),
            )
        )
    return found


def _swift_platforms(text: str, path: str) -> list[Platform]:
    return [
        Platform(path, m.group(1), m.group(3) or m.group(2).replace("_", "."))
        for m in _SWIFT_PLATFORM.finditer(text)
    ]


def _pbxproj_platforms(text: str, path: str) -> list[Platform]:
    return [
        Platform(path, _PBXPROJ_NAME[m.group(1)], m.group(2).strip('"'))
        for m in _PBXPROJ_TARGET.finditer(text)
    ]


def _csharp_platforms(text: str, path: str) -> list[Platform]:
    return [
        Platform(path, DOTNET, framework.strip())
        for m in _TARGET_FRAMEWORKS.finditer(text)
        for framework in m.group(1).split(";")
        if framework.strip()
    ]


def _python_platforms(text: str, path: str) -> list[Platform]:
    required = _toml(text).get("project", {}).get("requires-python")
    return [Platform(path, PYTHON, required)] if isinstance(required, str) else []


# 下限の探索範囲の規則。NEAREST は変更したファイルに最も近い祖先のものだけ、
# ALL_ANCESTORS は根までの全ての祖先のものを取る
NEAREST = "nearest"
ALL_ANCESTORS = "all_ancestors"


@dataclass(frozen=True)
class ManifestKind:
    """マニフェストの一種類についての知識。表 MANIFEST_KINDS の一行。

    name はファイル名の fnmatch の型。platforms が None の種類は配備の下限の対象ではない。
    container を持つ種類は、その型のディレクトリの中にファイルがある。
    """

    name: str
    language: str
    dependencies: Callable[[str, str], list[Dependency]] | None = None
    platforms: Callable[[str, str], list[Platform]] | None = None
    scope: str | None = None
    container: str | None = None

    def matches(self, file_name: str) -> bool:
        return fnmatchcase(file_name, self.name)


# ファイル名ごとの知識はこの表にだけ置く。上から順に、先に当たった行を使う
MANIFEST_KINDS = (
    ManifestKind("Package.swift", "swift", _swift, _swift_platforms, NEAREST),
    ManifestKind(
        "project.pbxproj",
        "swift",
        platforms=_pbxproj_platforms,
        scope=ALL_ANCESTORS,
        container="*.xcodeproj",
    ),
    ManifestKind("*.csproj", "csharp", _csharp, _csharp_platforms, NEAREST),
    ManifestKind(CENTRAL_FILE, "csharp", _csharp),
    ManifestKind(
        "Directory.Build.props", "csharp", platforms=_csharp_platforms, scope=ALL_ANCESTORS
    ),
    ManifestKind("pyproject.toml", "python", _python, _python_platforms, ALL_ANCESTORS),
    ManifestKind("package.json", "javascript", _javascript),
    ManifestKind("build.gradle", "java", _gradle),
    ManifestKind("build.gradle.kts", "java", _gradle),
    ManifestKind("pom.xml", "java", _pom),
)

_PLATFORM_KINDS = tuple(kind for kind in MANIFEST_KINDS if kind.platforms is not None)
# 資料が示す「下限を読むマニフェスト」と言語は、表の下限の抽出器を持つ行から導く
PLATFORM_FILES = tuple(kind.name for kind in _PLATFORM_KINDS)
PLATFORM_LANGUAGES = frozenset(kind.language for kind in _PLATFORM_KINDS)


def _kind(file_name: str) -> ManifestKind | None:
    return next((kind for kind in MANIFEST_KINDS if kind.matches(file_name)), None)


def _ancestors(path: str) -> list[PurePosixPath]:
    """path の親から根までのディレクトリを、近い順に返す。根は PurePosixPath(".")。"""
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts:
        # 木の外を指す道は、祖先として辿らない
        return []
    return list(pure.parents)


def _entries(root: Path, directory: PurePosixPath) -> list[str]:
    try:
        return sorted(entry.name for entry in (root / directory).iterdir())
    except OSError:
        # 変更前の木には、後から足したディレクトリも、消えたファイルの親も無いことがある
        return []


def _platform_scope(root: Path, paths: Iterable[str]) -> set[str]:
    """対象のファイルに効くマニフェストのパス。探索範囲の規則は表の scope が持つ。"""
    found: set[str] = set()
    for path in paths:
        settled: set[str] = set()
        for directory in _ancestors(path):
            names = _entries(root, directory)
            prefix = "" if directory == PurePosixPath() else f"{directory.as_posix()}/"
            for kind in _PLATFORM_KINDS:
                if kind.scope == NEAREST and kind.name in settled:
                    continue
                entry_pattern = kind.container or kind.name
                entries = [n for n in names if fnmatchcase(n, entry_pattern)]
                suffix = "" if kind.container is None else f"/{kind.name}"
                found.update(f"{prefix}{entry}{suffix}" for entry in entries)
                # 同じディレクトリの複数の csproj は全部取るので、
                # ディレクトリを抜けてから印を付ける
                if entries:
                    settled.add(kind.name)
    return found


@dataclass(frozen=True)
class Manifests:
    """一つの木から読んだマニフェストの内容。"""

    dependencies: tuple[Dependency, ...]
    platforms: tuple[Platform, ...]


def read(root: Path, changed_paths: Iterable[str] | None) -> Manifests:
    """木のマニフェストを一度走査して読む。

    依存の宣言は木の全体から取る。配備の下限は changed_paths に効くマニフェストだけから
    取り、changed_paths が None なら取らない（None を明示して渡す）。
    """
    scope = None if changed_paths is None else _platform_scope(root, changed_paths)
    found: list[Dependency] = []
    limits: dict[Platform, None] = {}
    for path in walk_tree_files(root, root):
        kind = _kind(PurePosixPath(path).name)
        if kind is None or "node_modules/" in f"/{path}":
            continue
        wants_platforms = kind.platforms is not None and scope is not None and path in scope
        if kind.dependencies is None and not wants_platforms:
            continue
        text = read_tree_text(root, path)
        if text is None:
            continue
        if kind.dependencies is not None:
            found.extend(kind.dependencies(text, path))
        if wants_platforms and kind.platforms is not None:
            # 同じファイルの同じ値は一件にまとめる
            limits.update(dict.fromkeys(kind.platforms(text, path)))
    central = {
        d.name.lower() for d in found if PurePosixPath(d.path).name == CENTRAL_FILE and d.version
    }
    # 中央のパッケージ管理では、版の無い PackageReference の宣言の本体は PackageVersion になる
    dependencies = tuple(
        d
        for d in found
        if not (
            d.language == "csharp"
            and d.version is None
            and d.name.lower() in central
            and PurePosixPath(d.path).name != CENTRAL_FILE
        )
    )
    return Manifests(dependencies, tuple(limits))


# 一語だけの一致は、`Microsoft.*` や `System.*` の無数のパッケージに当たってしまう
_CSHARP_BROAD = ("system", "microsoft")


def _match_csharp(candidates: list[Dependency], key: str) -> Dependency | None:
    if key.startswith("microsoft.aspnetcore"):
        # ASP.NET Core は SDK に含まれ、パッケージの宣言が無くても使える
        return None
    for d in candidates:
        name = d.name.lower()
        if name == key or name.startswith(f"{key}."):
            return d
    first = key.split(".", 1)[0]
    if first != key and first not in _CSHARP_BROAD:
        return next((d for d in candidates if d.name.lower() == first), None)
    return None


def match(dependencies: Sequence[Dependency], language: str, technology: str) -> Dependency | None:
    """技術の名前が宣言のパッケージ（Swift は product を含む）に当たれば、その宣言を返す。"""
    key = technology.lower()
    candidates = [d for d in dependencies if d.language == language]
    if language == "python":
        wanted = normalize_python(technology)
        return next((d for d in candidates if normalize_python(d.name) == wanted), None)
    if language == "swift":
        wanted_forms = {key, key.replace("-", "_")}
        return next(
            (
                d
                for d in candidates
                if {d.name.lower(), d.name.lower().replace("-", "_")} & wanted_forms
            ),
            None,
        )
    if language == "csharp":
        return _match_csharp(candidates, key)
    if language == "java":
        for d in candidates:
            group = d.name.lower().split(":", 1)[0]
            if group == key or group.startswith(f"{key}.") or key.startswith(f"{group}."):
                return d
        return None
    return next((d for d in candidates if d.name.lower() == key), None)


def changes(before: Sequence[Dependency], after: Sequence[Dependency]) -> list[dict[str, object]]:
    """足した、消した、版を変えたパッケージを、宣言のファイルと行とともに返す。"""

    def keyed(items: Sequence[Dependency]) -> dict[tuple[str, str, str], Dependency]:
        return {(d.path, d.language, d.name.lower()): d for d in items if d.kind == PACKAGE}

    old, new = keyed(before), keyed(after)
    result: list[dict[str, object]] = []
    for key in sorted(set(old) | set(new)):
        previous, current = old.get(key), new.get(key)
        shown = current or previous
        if shown is None:
            continue
        kind = change_of(
            had_before=previous is not None,
            has_after=current is not None,
            equal=(
                previous is not None
                and current is not None
                and previous.version == current.version
            ),
        )
        if kind == UNCHANGED:
            # 版が同じ宣言は変化ではない
            continue
        result.append(
            {
                "change": kind,
                "language": shown.language,
                "name": shown.name,
                "path": shown.path,
                "line": shown.line,
                "before_version": None if previous is None else previous.version,
                "after_version": None if current is None else current.version,
            }
        )
    return result
