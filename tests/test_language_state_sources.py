"""選べる言語の状態を、起動の判定、アダプタ、`languages list`、範囲の案内が同じ値から引く試験。"""

import json
from pathlib import Path
from typing import NamedTuple

import pytest
from language_plugin_support import (
    Installer,
    approved,
    builtin_states_ready,
    builtin_states_without_grammar,
    external_plugin,
    fake_library,
    grammar_absent_detail,
)

from jigane import commands
from jigane.commands.exit_codes import EXIT_PROCESSING_FAILURE
from jigane.commands.unparsed_files import UnparsedFiles, unparsed_files, unparsed_text
from jigane.languages import grammars, registry
from jigane.languages.grammars import FETCH_COMMAND
from jigane.languages.states import (
    LanguageGrammarAbsent,
    LanguageReady,
    LanguageState,
    LanguageUnavailable,
    ReasonKind,
)
from jigane.reviews.lifecycle import create_feature_review

NOTICE_PREFIX = "jigane: 文法を取得していない言語: "


def _external_ready(name: str, suffix: str) -> LanguageReady:
    return LanguageReady(external_plugin(name, suffix))


def _external_faulty(name: str, kind: ReasonKind, detail: str) -> LanguageUnavailable:
    return LanguageUnavailable(name, kind, detail)


def _external_absent(name: str, suffix: str) -> LanguageGrammarAbsent:
    return LanguageGrammarAbsent(external_plugin(name, suffix), grammar_absent_detail(name))


def test_external_grammar_absent_does_not_stop_startup_and_names_the_language(
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry.set_states((*builtin_states_ready(), _external_absent("zig", ".zig")))
    registry.require_ready()
    err = capsys.readouterr().err
    assert err == f"{NOTICE_PREFIX}zig（そのファイルは対応外の言語として扱います）\n"


def test_external_grammar_absent_returns_no_adapter() -> None:
    registry.set_states((*builtin_states_ready(), _external_absent("zig", ".zig")))
    assert registry.adapter_for("src/a.zig") is None
    assert "zig" not in {adapter.name for adapter in registry.adapters()}


def test_external_grammar_absent_files_are_guided_to_languages_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "shop"
    (project / "src").mkdir(parents=True)
    (project / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (project / "src" / "b.zig").write_text("const x = 1;\n", encoding="utf-8")
    registry.set_states((*builtin_states_ready(), _external_absent("zig", ".zig")))
    review = create_feature_review(project, ["src"], "p")
    found = unparsed_files(review)
    assert found == UnparsedFiles({"zig": ("src/b.zig",)}, {})
    lines = unparsed_text(found)
    assert "文法を取得していない言語:" in lines
    assert "`jigane languages fetch zig` で取得すると解析できます。" in lines


@pytest.mark.parametrize(
    ("kind", "detail"),
    [
        (ReasonKind.LOAD_ERROR, "ImportError: boom"),
        (ReasonKind.VALIDATION_ERROR, "宣言が不正です"),
        (ReasonKind.GRAMMAR_ERROR, "文法 zig のハッシュが宣言と合いません"),
    ],
)
def test_external_faulty_language_still_stops_startup(
    kind: ReasonKind, detail: str, capsys: pytest.CaptureFixture[str]
) -> None:
    faulty = _external_faulty("zig", kind, detail)
    registry.set_states((*builtin_states_ready(), faulty))
    with pytest.raises(registry.LanguagePluginUnavailableError, match="zig"):
        registry.require_ready()
    assert commands.main(["list"]) == EXIT_PROCESSING_FAILURE
    err = capsys.readouterr().err
    assert "zig" in err
    assert "jigane languages disable zig" in err
    assert NOTICE_PREFIX not in err


BUILTIN_NAMES = ("python", "java", "swift", "csharp")


def _declared_spec(grammar_hash: str) -> str:
    return (
        f'GrammarSpec(pack_version="{grammars.pack_version()}", '
        f'hashes={{"{grammars.platform_key()}": "{grammar_hash}"}})'
    )


def _enable_declared_zig(tmp_path: Path, install: Installer) -> None:
    install(
        "zig",
        suffixes=(".zig",),
        grammar="zig_for_state_tests",
        spec_expr=_declared_spec("0" * 64),
    )
    path = tmp_path / "home" / "languages.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = approved("zig", "fakelang_zig-dist").to_json()
    path.write_text(json.dumps({"enabled": [record]}), encoding="utf-8")


def _list_rows(capsys: pytest.CaptureFixture[str]) -> dict[str, dict[str, object]]:
    assert commands.main(["languages", "list", "--json"]) == 0
    languages = json.loads(capsys.readouterr().out)["languages"]
    return {row["name"]: row for row in languages}


def test_grammar_absent_rows_are_their_own_state_with_a_fetch_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install: Installer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(grammars, "cache_root", lambda: tmp_path / "empty-grammars")
    _enable_declared_zig(tmp_path, install)
    rows = _list_rows(capsys)
    for name in (*BUILTIN_NAMES, "zig"):
        row = rows[name]
        assert row["state"] == "grammar_absent"
        assert row["reason_kind"] is None
        assert row["suffixes"] == []
        assert f"{FETCH_COMMAND} {name}" in str(row["detail"])


def test_grammar_absent_row_has_the_same_keys_as_a_faulty_grammar_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install: Installer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = tmp_path / "empty-grammars"
    monkeypatch.setattr(grammars, "cache_root", lambda: empty)
    _enable_declared_zig(tmp_path, install)
    absent_rows = _list_rows(capsys)
    # 宣言のハッシュと合わない実物を置くと、zig は「文法が無い」でなく「文法の誤り」になる
    fake_library(empty, "zig_for_state_tests", b"not the declared library")
    registry.set_states(None)
    faulty_rows = _list_rows(capsys)
    assert faulty_rows["zig"]["state"] == "unavailable"
    assert faulty_rows["zig"]["reason_kind"] == "grammar_error"
    assert absent_rows["zig"]["detail"] != faulty_rows["zig"]["detail"]
    shapes = {tuple(row) for row in (absent_rows["zig"], faulty_rows["zig"], absent_rows["swift"])}
    assert len(shapes) == 1


# 一行が一つの状態に当たる。引く元は、起動の判定、アダプタ、`languages list`、language_for_suffix
_STOP = "stop"
_NOTICE = "notice"
_QUIET = "quiet"


def _states_for(case: str) -> tuple[LanguageState, ...]:
    if case == "builtin_ready":
        return tuple(builtin_states_ready())
    if case == "builtin_absent":
        return tuple(builtin_states_without_grammar("swift"))
    if case == "external_ready":
        return (*builtin_states_ready(), _external_ready("zig", ".zig"))
    if case == "external_absent":
        return (*builtin_states_ready(), _external_absent("zig", ".zig"))
    faulty = _external_faulty(
        "zig", ReasonKind.GRAMMAR_ERROR, "文法 zig のハッシュが宣言と合いません"
    )
    return (*builtin_states_ready(), faulty)


class _Scene(NamedTuple):
    """一つの状態に当たる場面と、各所が返すと期待する値。"""

    case: str
    path: str
    startup: str
    has_adapter: bool
    row_state: str
    row_reason: str | None
    for_suffix: tuple[str, type] | None


_SCENES = [
    _Scene(
        case="builtin_ready",
        path="a.py",
        startup=_QUIET,
        has_adapter=True,
        row_state="ready",
        row_reason=None,
        for_suffix=("python", LanguageReady),
    ),
    _Scene(
        case="builtin_absent",
        path="A.swift",
        startup=_NOTICE,
        has_adapter=False,
        row_state="grammar_absent",
        row_reason=None,
        for_suffix=("swift", LanguageGrammarAbsent),
    ),
    _Scene(
        case="external_ready",
        path="a.zig",
        startup=_QUIET,
        has_adapter=True,
        row_state="ready",
        row_reason=None,
        for_suffix=("zig", LanguageReady),
    ),
    _Scene(
        case="external_absent",
        path="a.zig",
        startup=_NOTICE,
        has_adapter=False,
        row_state="grammar_absent",
        row_reason=None,
        for_suffix=("zig", LanguageGrammarAbsent),
    ),
    _Scene(
        case="external_faulty",
        path="a.zig",
        startup=_STOP,
        has_adapter=False,
        row_state="unavailable",
        row_reason="grammar_error",
        for_suffix=None,
    ),
]


@pytest.mark.parametrize("scene", [pytest.param(scene, id=scene.case) for scene in _SCENES])
def test_every_source_reads_the_same_language_status(
    scene: _Scene, capsys: pytest.CaptureFixture[str]
) -> None:
    case, path, startup, for_suffix = scene.case, scene.path, scene.startup, scene.for_suffix
    registry.set_states(_states_for(case))
    name = "zig" if for_suffix is None else for_suffix[0]
    try:
        registry.require_ready()
    except registry.LanguagePluginUnavailableError:
        outcome = _STOP
    else:
        outcome = _NOTICE if NOTICE_PREFIX in capsys.readouterr().err else _QUIET
    assert outcome == startup
    assert (registry.adapter_for(path) is not None) is scene.has_adapter
    row = _list_rows(capsys)[name]
    assert (row["state"], row["reason_kind"]) == (scene.row_state, scene.row_reason)
    found = registry.language_for_suffix(path)
    if for_suffix is None:
        assert found is None
    else:
        assert found is not None
        assert (found.plugin.adapter.name, type(found)) == for_suffix
