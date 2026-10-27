import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from review_fakes import make_review

from jigane.commands.subcommands import technology_list as technology_command
from jigane.languages.base import Import, ImportAdapter, parse
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.java import JavaAdapter
from jigane.languages.python import PythonAdapter
from jigane.languages.swift import SwiftAdapter
from jigane.reviews.lifecycle import FeatureOrigin, Review
from jigane.storage.store import ReviewKind
from jigane.technology_list import manifests, materials, tables
from jigane.technology_list.build import build_model
from jigane.technology_list.tables import platform_coverage as _platform_coverage


def _imports(adapter: ImportAdapter, text: str) -> list[Import]:
    source = text.encode()
    return adapter.imports(parse(adapter, source).root_node, source)


def test_swift_imports() -> None:
    found = _imports(
        SwiftAdapter(),
        "import Foundation\n@testable import MyApp\nimport struct Foo.Bar\n",
    )
    assert found == [Import("Foundation", 1), Import("MyApp", 2), Import("Foo.Bar", 3)]
    assert SwiftAdapter().technology_of("Foo.Bar") == "Foo"


def test_csharp_imports() -> None:
    found = _imports(
        CSharpAdapter(),
        "using System;\nusing static System.Math;\nusing Alias = System.Text.StringBuilder;\n"
        "global using A.B.C;\nnamespace X { using Q.R.S; }\n",
    )
    assert found == [
        Import("System", 1),
        Import("System.Math", 2),
        Import("System.Text.StringBuilder", 3),
        Import("A.B.C", 4),
        Import("Q.R.S", 5),
    ]
    adapter = CSharpAdapter()
    assert adapter.technology_of("System.Text.StringBuilder") == "System.Text"
    assert adapter.technology_of("System") == "System"


def test_python_imports() -> None:
    found = _imports(
        PythonAdapter(),
        "import a.b\nimport c.d as e, f\nfrom g.h import i\nfrom . import j\nfrom ..k import m\n",
    )
    assert found == [
        Import("a.b", 1),
        Import("c.d", 2),
        Import("f", 2),
        Import("g.h", 3),
        Import(".", 4),
        Import("..k", 5),
    ]
    assert PythonAdapter().technology_of("g.h") == "g"


def test_java_imports() -> None:
    found = _imports(JavaAdapter(), "import a.b.C;\nimport static a.b.D.m;\nimport x.y.*;\n")
    assert found == [Import("a.b.C", 1), Import("a.b.D.m", 2), Import("x.y", 3)]
    assert JavaAdapter().technology_of("a.b.D.m") == "a.b"
    assert JavaAdapter().technology_of("a") == "a"


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")


@pytest.fixture
def review(tmp_path: Path) -> Review:
    after, before = tmp_path / "after", tmp_path / "before"
    _write(
        after,
        {
            "Package.swift": 'let p = Package(targets: [\n  .target(\n    name: "Core"),\n])\n',
            "Sources/App.swift": "import SwiftUI\nimport Core\nimport UIKit\n\nstruct A {}\n",
            "Sources/Other.swift": "import SwiftUI\n",
            "Server/Acme.Server.csproj": "<Project />",
            "Server/Run.cs": (
                "using System.Text;\nusing Acme.Server.Util;\nusing Acme.Server.Db;\n"
            ),
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": ("import os\nimport pkg.util\nfrom . import x\nimport requests\n"),
            "src/pkg/gone.py": "import json\n",
            "java/com/acme/App.java": (
                "package com.acme.app;\nimport com.acme.core.X;\nimport java.util.List;\n"
            ),
        },
    )
    _write(
        before,
        {
            "Sources/App.swift": "import SwiftUI\nimport UIKit\n",
            "Sources/Other.swift": "import SwiftUI\n",
            "Server/Run.cs": "using System.Text;\nusing Acme.Server.Util;\n",
            "src/pkg/mod.py": "import os\nimport json\n",
            "src/pkg/gone.py": "import json\n",
        },
    )
    paths = [
        "Sources/App.swift",
        "Sources/Other.swift",
        "Server/Run.cs",
        "src/pkg/mod.py",
        "src/pkg/gone.py",
        "java/com/acme/App.java",
    ]
    return make_review(after, paths, base_tree=before, kind=ReviewKind.RANGE)


def _model(review: Review) -> dict[str, Any]:
    return build_model(review)


def _by_name(model: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(t["language"], t["name"]): t for t in model["technologies"]}


def test_classification_with_evidence(review: Review) -> None:
    technologies = _by_name(_model(review))
    core = technologies[("swift", "Core")]
    assert core["classification"] == "same_repository"
    assert core["evidence"] == {"path": "Package.swift", "line": 3}
    assert technologies[("swift", "SwiftUI")]["classification"] == "language_os"
    assert technologies[("swift", "SwiftUI")]["evidence"] is None
    assert technologies[("csharp", "Acme.Server")]["evidence"] == {
        "path": "Server/Acme.Server.csproj",
        "line": 1,
    }
    assert technologies[("csharp", "System.Text")]["classification"] == "language_os"
    assert technologies[("python", "pkg")]["evidence"] == {
        "path": "src/pkg/__init__.py",
        "line": 1,
    }
    assert technologies[("python", "requests")]["classification"] == "language_os"
    assert technologies[("java", "com.acme")]["classification"] == "same_repository"
    assert technologies[("java", "java.util")]["classification"] == "language_os"


def test_relative_imports_are_not_technologies(review: Review) -> None:
    names = {name for _, name in _by_name(_model(review))}
    assert "" not in names
    assert not any(name.startswith(".") for name in names)


def test_usage_and_change(review: Review) -> None:
    technologies = _by_name(_model(review))
    swiftui = technologies[("swift", "SwiftUI")]
    assert swiftui["after"] == {"files": 2, "lines": 6}
    assert swiftui["before"] == {"files": 2, "lines": 3}
    assert swiftui["change"] == "unchanged"
    # 変更前に無く、変更後にだけある
    assert technologies[("swift", "Core")]["change"] == "added"
    assert technologies[("python", "requests")]["change"] == "added"
    # 変更後に無く、変更前にだけある
    removed = technologies[("python", "json")]
    assert removed["after"] == {"files": 1, "lines": 1}
    assert removed["change"] == "unchanged"
    # 同じファイルで import が増えた
    assert technologies[("csharp", "Acme.Server")]["after"]["files"] == 1
    assert technologies[("csharp", "Acme.Server")]["change"] == "added"


def test_removed_technology(review: Review) -> None:
    (Path(review.repository_root) / "src/pkg/gone.py").write_text("import os\n", encoding="utf-8")
    technologies = _by_name(_model(review))
    json_usage = technologies[("python", "json")]
    assert json_usage["after"] == {"files": 0, "lines": 0}
    assert json_usage["before"]["files"] == 2
    assert json_usage["change"] == "removed"


def test_feature_review_has_no_before(review: Review) -> None:
    review.origin = FeatureOrigin()
    for technology in _model(review)["technologies"]:
        assert technology["before"] is None
        assert technology["change"] is None


def test_cli_json_and_table(
    review: Review, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(technology_command, "load_review", lambda _id: review)
    monkeypatch.setattr(technology_command, "resolve_review", lambda text: text)
    parser = argparse.ArgumentParser()
    technology_command.register(parser.add_subparsers())
    args = parser.parse_args(["technology-list", "r#1", "--json"])
    assert args.handler(args, None) == 0
    model = json.loads(capsys.readouterr().out)
    assert any(t["name"] == "Core" and t["language"] == "swift" for t in model["technologies"])
    args = parser.parse_args(["technology-list", "r#1"])
    assert args.handler(args, None) == 0
    table = capsys.readouterr().out
    assert "同じリポジトリ" in table
    assert "Package.swift:3" in table
    assert "増えた" in table
    assert "\n配備の下限\n下限を読むマニフェスト: Package.swift" in table


def test_cli_and_materials_share_row_values() -> None:
    model: dict[str, Any] = {
        "technologies": [{"language": "swift"}, {"language": "java"}],
        "manifest_changes": [
            {
                "change": "version_changed",
                "language": "swift",
                "name": "Alamofire",
                "before_version": "5.0.0",
                "after_version": "5.1.0",
                "path": "Package.swift",
                "line": 7,
            }
        ],
        "bridges": [
            {
                "via": "base",
                "name": "Spinner",
                "bridge": "UIViewRepresentable",
                "body_lines": {"makeUIView": 3},
                "type_lines": 9,
                "members": 2,
                "from": "UIKit",
                "to": "SwiftUI",
                "path": "A/Spinner.swift",
                "line": 4,
            }
        ],
        "platforms": [
            {
                "path": "Package.swift",
                "platform": "iOS",
                "minimum": "13.0",
                "before_minimum": "12.0",
                "change": "version_changed",
            }
        ],
    }
    model["technologies"] = []
    sections = tables.sections(model)
    assert [s.key for s in sections] == [
        "technologies",
        "manifest_changes",
        "bridges",
        "platforms",
    ]
    for section in sections[1:]:
        assert section.rows
        cli_text = technology_command._section(section.to_dict())
        markdown = materials._section(section.to_dict())
        for row in section.rows:
            assert all(cell in cli_text for cell in row)
            assert "| " + " | ".join(row) + " |" in markdown
        assert all(len(row) == len(section.header) == len(section.kinds) for row in section.rows)
        assert len(section.row_changes) == len(section.rows)
    # 変化の値は文言とは別に行ごとに持ち、色はこの値で決まる
    assert sections[1].row_changes == [c["change"] for c in model["manifest_changes"]]
    assert sections[3].row_changes == ["version_changed"]
    assert sections[2].row_changes == [None]
    # 見つけ方は橋渡しの列に出る
    assert sections[2].rows[0][5] == "準拠・継承"


def _deps(tmp_path: Path, files: dict[str, str]) -> list[manifests.Dependency]:
    _write(tmp_path, files)
    return list(manifests.read(tmp_path, None).dependencies)


def _find(deps: list[manifests.Dependency], name: str) -> manifests.Dependency:
    return next(d for d in deps if d.name == name)


def test_read_swift_manifest(tmp_path: Path) -> None:
    deps = _deps(
        tmp_path,
        {
            "Package.swift": (
                "let p = Package(\n  dependencies: [\n"
                '    .package(url: "https://github.com/a/swift-log.git", from: "1.5.0"),\n'
                '    .package(url: "https://github.com/a/Alamofire", exact: "5.0.0"),\n'
                '    .package(url: "https://github.com/a/Kit", .upToNextMajor(from: "2.1.0")),\n'
                '  ],\n  targets: [.target(name: "T", dependencies: [\n'
                '    .product(name: "Logging", package: "swift-log")])])\n'
            )
        },
    )
    assert (_find(deps, "swift-log").version, _find(deps, "swift-log").line) == ("1.5.0", 3)
    assert _find(deps, "Alamofire").version == "5.0.0"
    assert _find(deps, "Kit").version == "2.1.0"
    assert _find(deps, "Logging").kind == manifests.PRODUCT
    assert manifests.match(deps, "swift", "Logging") is not None
    assert manifests.match(deps, "swift", "alamofire") is not None


def test_read_csharp_manifests_and_central_management(tmp_path: Path) -> None:
    deps = _deps(
        tmp_path,
        {
            "A/A.csproj": (
                "<Project>\n"
                '  <PackageReference Include="ErrorOr" Version="2.0.1" />\n'
                '  <PackageReference Include="Xunit" />\n'
                "</Project>\n"
            ),
            "Directory.Packages.props": (
                '<Project>\n  <PackageVersion Include="Xunit" Version="2.9.0" />\n</Project>\n'
            ),
        },
    )
    assert _find(deps, "ErrorOr").version == "2.0.1"
    xunit = [d for d in deps if d.name == "Xunit"]
    assert [(d.path, d.version, d.line) for d in xunit] == [
        ("Directory.Packages.props", "2.9.0", 2)
    ]
    assert manifests.match(deps, "csharp", "Xunit") is not None
    assert manifests.match(deps, "csharp", "ErrorOr") is not None
    assert manifests.match(deps, "csharp", "Microsoft.AspNetCore") is None
    assert manifests.match(deps, "csharp", "System.Text") is None


def test_csharp_prefix_match(tmp_path: Path) -> None:
    deps = _deps(
        tmp_path,
        {
            "A.csproj": (
                '<PackageReference Include="Vogen" Version="1" />\n'
                '<PackageReference Include="Serilog.Sinks.Console" Version="5" />\n'
                '<PackageReference Include="Microsoft.AspNetCore.Mvc.Testing" Version="8" />\n'
            )
        },
    )
    assert manifests.match(deps, "csharp", "Serilog.Sinks") is not None
    assert manifests.match(deps, "csharp", "Vogen.Generators") is not None
    assert manifests.match(deps, "csharp", "Microsoft.AspNetCore") is None


def test_read_python_manifest(tmp_path: Path) -> None:
    deps = _deps(
        tmp_path,
        {
            "pyproject.toml": (
                "[project]\n"
                "dependencies = [\n"
                '  "Tree_Sitter>=0.23",\n'
                "  \"jinja2[i18n]==3.1; python_version>'3'\",\n"
                "]\n"
                "[project.optional-dependencies]\n"
                'web = ["Flask.Cors"]\n'
                "[dependency-groups]\n"
                'dev = ["pytest>=8", {include-group = "web"}]\n'
            )
        },
    )
    assert _find(deps, "Tree_Sitter").line == 3
    assert _find(deps, "Tree_Sitter").version == ">=0.23"
    assert _find(deps, "jinja2").version == "==3.1"
    assert manifests.match(deps, "python", "tree_sitter") is not None
    assert manifests.match(deps, "python", "flask_cors") is not None
    assert manifests.match(deps, "python", "pytest") is not None
    assert manifests.match(deps, "python", "requests") is None


def test_read_javascript_manifest(tmp_path: Path) -> None:
    deps = _deps(
        tmp_path,
        {
            "package.json": (
                '{\n  "dependencies": {"react": "^18"},\n'
                '  "devDependencies": {\n    "vitest": "1.0.0"\n  }\n}\n'
            )
        },
    )
    assert (_find(deps, "react").version, _find(deps, "react").line) == ("^18", 2)
    assert _find(deps, "vitest").line == 4


def test_read_java_manifests(tmp_path: Path) -> None:
    deps = _deps(
        tmp_path,
        {
            "build.gradle.kts": 'dependencies {\n  implementation("org.foo:bar:1.2")\n}\n',
            "build.gradle": "dependencies {\n  testImplementation 'io.baz:qux:3.0'\n}\n",
            "pom.xml": (
                "<project><dependencies>\n<dependency>\n<groupId>com.x</groupId>\n"
                "<artifactId>y</artifactId>\n<version>9</version>\n</dependency>\n"
                "</dependencies></project>\n"
            ),
        },
    )
    assert (_find(deps, "org.foo:bar").version, _find(deps, "org.foo:bar").line) == ("1.2", 2)
    assert _find(deps, "io.baz:qux").version == "3.0"
    assert (_find(deps, "com.x:y").version, _find(deps, "com.x:y").line) == ("9", 2)
    assert manifests.match(deps, "java", "org.foo") is not None
    assert manifests.match(deps, "java", "java.util") is None


def test_external_classification_and_changes(tmp_path: Path) -> None:
    after, before = tmp_path / "after", tmp_path / "before"
    _write(
        after,
        {
            "pyproject.toml": '[project]\ndependencies = ["requests>=3", "rich"]\n',
            "src/mod.py": "import requests\nimport rich\nimport json\n",
        },
    )
    _write(
        before,
        {
            "pyproject.toml": '[project]\ndependencies = ["requests>=2", "old"]\n',
            "src/mod.py": "import json\n",
        },
    )
    review = make_review(after, ["src/mod.py"], base_tree=before, kind=ReviewKind.RANGE)
    model = build_model(review)
    technologies = _by_name(model)
    requests = technologies[("python", "requests")]
    assert requests["classification"] == "external"
    assert requests["evidence"] == {
        "path": "pyproject.toml",
        "line": 2,
        "package": "requests",
        "version": ">=3",
    }
    assert technologies[("python", "json")]["classification"] == "language_os"
    changes = {(c["name"], c["change"]): c for c in model["manifest_changes"]}
    assert set(changes) == {
        ("requests", "version_changed"),
        ("rich", "added"),
        ("old", "removed"),
    }
    assert changes[("requests", "version_changed")]["before_version"] == ">=2"
    assert changes[("old", "removed")]["line"] == 2
    assert changes[("rich", "added")]["path"] == "pyproject.toml"


def test_unchanged_manifest_gives_empty_changes(review: Review) -> None:
    assert _model(review)["manifest_changes"] == []
    review.origin = FeatureOrigin()
    assert _model(review)["manifest_changes"] is None


def _platforms(
    tmp_path: Path, files: dict[str, str], paths: list[str] | None = None
) -> list[tuple[str, str, str]]:
    _write(tmp_path, files)
    paths = ["a.py"] if paths is None else paths
    return [(p.path, p.platform, p.minimum) for p in manifests.read(tmp_path, paths).platforms]


def test_read_swift_package_platforms(tmp_path: Path) -> None:
    found = _platforms(
        tmp_path,
        {
            "Package.swift": "let package = Package(\n"
            '  platforms: [.iOS(.v13), .macOS(.v10_15), .tvOS("15.0")],\n)\n'
        },
    )
    assert found == [
        ("Package.swift", "iOS", "13"),
        ("Package.swift", "macOS", "10.15"),
        ("Package.swift", "tvOS", "15.0"),
    ]


def test_read_pbxproj_targets_merges_equal_values(tmp_path: Path) -> None:
    path = "App.xcodeproj/project.pbxproj"
    found = _platforms(
        tmp_path,
        {
            path: "IPHONEOS_DEPLOYMENT_TARGET = 13.0;\n"
            "IPHONEOS_DEPLOYMENT_TARGET = 13.0;\nIPHONEOS_DEPLOYMENT_TARGET = 15.0;\n"
            "MACOSX_DEPLOYMENT_TARGET = 12.0;\n"
        },
        ["x.swift"],
    )
    assert found == [(path, "iOS", "13.0"), (path, "iOS", "15.0"), (path, "macOS", "12.0")]


def test_read_csproj_single_and_multiple_target_frameworks(tmp_path: Path) -> None:
    found = _platforms(
        tmp_path,
        {
            "A/A.csproj": "<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework>"
            "</PropertyGroup></Project>",
            "B/B.csproj": "<Project><PropertyGroup><TargetFrameworks>net6.0; net8.0"
            "</TargetFrameworks></PropertyGroup></Project>",
            "Directory.Build.props": (
                "<Project><TargetFramework>net7.0</TargetFramework></Project>"
            ),
        },
        ["A/x.cs", "B/y.cs"],
    )
    assert ("A/A.csproj", ".NET", "net8.0") in found
    assert ("B/B.csproj", ".NET", "net6.0") in found
    assert ("B/B.csproj", ".NET", "net8.0") in found
    assert ("Directory.Build.props", ".NET", "net7.0") in found


def test_read_pyproject_requires_python(tmp_path: Path) -> None:
    found = _platforms(tmp_path, {"pyproject.toml": '[project]\nrequires-python = ">=3.11"\n'})
    assert found == [("pyproject.toml", "Python", ">=3.11")]


def test_platforms_in_model_show_changes(tmp_path: Path) -> None:
    after, before = tmp_path / "after", tmp_path / "before"
    _write(after, {"Package.swift": "platforms: [.iOS(.v14), .macOS(.v12)]\n", "a.py": "\n"})
    _write(before, {"Package.swift": "platforms: [.iOS(.v13)]\n", "a.py": "\n"})
    review = make_review(after, ["a.py"], base_tree=before, kind=ReviewKind.RANGE)
    rows = {p["platform"]: p for p in build_model(review)["platforms"]}
    assert rows["iOS"] == {
        "path": "Package.swift",
        "platform": "iOS",
        "minimum": "14",
        "before_minimum": "13",
        "change": "version_changed",
    }
    assert rows["macOS"]["change"] == "added"
    review.origin = FeatureOrigin()
    feature = build_model(review)["platforms"]
    assert {p["change"] for p in feature} == {None}


_PACKAGE = "platforms: [.iOS(.v{})]\n"


def test_platforms_only_for_the_package_holding_the_file(tmp_path: Path) -> None:
    files = {
        "A/Package.swift": _PACKAGE.format(13),
        "A/Sources/x.swift": "\n",
        "B/Package.swift": _PACKAGE.format(15),
        "B/Sources/y.swift": "\n",
    }
    found = _platforms(tmp_path, files, ["A/Sources/x.swift"])
    assert found == [("A/Package.swift", "iOS", "13")]


def test_platforms_take_ancestor_directory_build_props_and_pyproject(tmp_path: Path) -> None:
    props = "<Project><TargetFramework>net7.0</TargetFramework></Project>"
    csproj = "<Project><TargetFramework>net8.0</TargetFramework></Project>"
    files = {
        "Directory.Build.props": props,
        "pyproject.toml": '[project]\nrequires-python = ">=3.11"\n',
        "sub/pyproject.toml": '[project]\nrequires-python = ">=3.12"\n',
        "other/pyproject.toml": '[project]\nrequires-python = ">=3.9"\n',
        "sub/App/App.csproj": csproj,
        "sub/App/Other.csproj": csproj,
        "sub/App/Code.cs": "\n",
    }
    found = _platforms(tmp_path, files, ["sub/App/Code.cs"])
    assert {path for path, _, _ in found} == {
        "Directory.Build.props",
        "pyproject.toml",
        "sub/pyproject.toml",
        "sub/App/App.csproj",
        "sub/App/Other.csproj",
    }


def test_platforms_take_only_nearest_package_swift(tmp_path: Path) -> None:
    files = {
        "Package.swift": _PACKAGE.format(12),
        "Inner/Package.swift": _PACKAGE.format(14),
        "Inner/x.swift": "\n",
    }
    assert _platforms(tmp_path, files, ["Inner/x.swift"]) == [("Inner/Package.swift", "iOS", "14")]


def test_platforms_take_pbxproj_beside_an_ancestor(tmp_path: Path) -> None:
    files = {
        "App/App.xcodeproj/project.pbxproj": "IPHONEOS_DEPLOYMENT_TARGET = 13.0;\n",
        "App/Sources/x.swift": "\n",
        "Other/Other.xcodeproj/project.pbxproj": "IPHONEOS_DEPLOYMENT_TARGET = 16.0;\n",
    }
    found = _platforms(tmp_path, files, ["App/Sources/x.swift"])
    assert found == [("App/App.xcodeproj/project.pbxproj", "iOS", "13.0")]


def test_platforms_empty_without_a_matching_manifest(tmp_path: Path) -> None:
    files = {"B/Package.swift": _PACKAGE.format(15), "A/x.swift": "\n"}
    assert _platforms(tmp_path, files, ["A/x.swift"]) == []


def test_platforms_compare_narrowed_trees_and_keep_removed_files(tmp_path: Path) -> None:
    after, before = tmp_path / "after", tmp_path / "before"
    _write(after, {"A/Package.swift": _PACKAGE.format(14), "B/Package.swift": _PACKAGE.format(9)})
    _write(
        before,
        {
            "A/Package.swift": _PACKAGE.format(13),
            "B/Package.swift": _PACKAGE.format(9),
            "A/gone.swift": "\n",
        },
    )
    review = make_review(after, ["A/gone.swift"], base_tree=before, kind=ReviewKind.RANGE)
    rows = build_model(review)["platforms"]
    assert [(r["path"], r["minimum"], r["before_minimum"], r["change"]) for r in rows] == [
        ("A/Package.swift", "14", "13", "version_changed")
    ]


def test_platform_constants_are_derived_from_the_kind_table() -> None:
    # 下限を読む名前と言語は、表で下限の抽出器を持つ行と両方向で一致する
    with_platforms = [k for k in manifests.MANIFEST_KINDS if k.platforms is not None]
    assert tuple(k.name for k in with_platforms) == manifests.PLATFORM_FILES
    assert {k.language for k in with_platforms} == manifests.PLATFORM_LANGUAGES
    for name in manifests.PLATFORM_FILES:
        kind = manifests._kind(name.replace("*", "x"))
        assert kind is not None
        assert kind.platforms is not None
        assert kind.scope is not None
    for kind in manifests.MANIFEST_KINDS:
        if kind.platforms is None:
            assert kind.name not in manifests.PLATFORM_FILES
            assert kind.scope is None
    package_json = manifests._kind("package.json")
    assert package_json is not None
    assert package_json.platforms is None
    assert manifests._kind("unknown.toml") is None


def test_platform_coverage_line_names_unread_languages() -> None:
    technologies = [{"language": "java"}, {"language": "swift"}, {"language": "javascript"}]
    coverage = _platform_coverage(technologies)
    assert "Package.swift" in coverage["manifests"]
    assert "pyproject.toml" in coverage["manifests"]
    assert coverage["unread_languages"] == ["java", "javascript"]
    assert _platform_coverage([{"language": "swift"}])["unread_languages"] == []


def test_short_section_lists_only_changed_technologies(review: Review) -> None:
    text = materials.short_section_text(review)
    assert "| 技術 | 分類 | 変化 | 根拠 |" in text
    assert "| Core | 同じリポジトリ | 増えた | Package.swift:3 |" in text
    assert "| requests | 言語と OS | 増えた | - |" in text
    assert "| json | 言語と OS | 使うファイルが減った（2 から 1） | - |" in text
    # 変化の無い技術の行は省き、件数だけを添える
    assert "SwiftUI" not in text
    assert "変化の無い技術は 4 件で、表から省いた。" in text
    # 全体の表の列（行数、変更前の欄）と他の節は持たない
    assert "#### " not in text
    assert "変更前(ファイル/行)" not in text
    full = "\n\n".join(materials._section(s) for s in build_model(review)["sections"])
    assert len(text) < len(full)


def test_short_section_in_feature_review_skips_language_and_os(review: Review) -> None:
    review.origin = FeatureOrigin()
    text = materials.short_section_text(review)
    assert "| Core | 同じリポジトリ | 対象のファイルが使う |" in text
    assert "SwiftUI" not in text


def test_subcommand_is_technology_list_only() -> None:
    parser = argparse.ArgumentParser()
    technology_command.register(parser.add_subparsers())
    with pytest.raises(SystemExit):
        parser.parse_args(["technology", "r#1"])
    assert parser.parse_args(["technology-list", "r#1"]).review == "r#1"
