import hashlib
import json
from pathlib import Path

import pytest
import tree_sitter_language_pack
from language_plugin_support import Installer
from test_grammars import _fake_library

from jigane import commands
from jigane.languages import fetch_main, grammars, queries, registry
from jigane.languages.base import grammar_target
from jigane.languages.plugin import GrammarSpec
from jigane.languages.plugins import LanguageUnavailable, ReasonKind


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _spec(
    content: bytes = b"zig-library",
    *,
    pack_version: str | None = None,
    platform: str | None = None,
) -> GrammarSpec:
    return GrammarSpec(
        pack_version=pack_version or grammars.pack_version(),
        hashes={platform or grammars.platform_key(): _digest(content)},
    )


def _spec_expr(spec: GrammarSpec) -> str:
    return f"GrammarSpec(pack_version={spec.pack_version!r}, hashes={dict(spec.hashes)!r})"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "store"
    monkeypatch.setattr(grammars, "cache_root", lambda: root)
    grammars._checked.clear()
    return root


def _enable_zig(
    install: Installer, tmp_path: Path, spec: GrammarSpec, *, grammar: str = "zig"
) -> None:
    install("zig", suffixes=(".zig",), grammar=grammar, spec_expr=_spec_expr(spec))
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "languages.json").write_text(
        json.dumps(
            {"enabled": [{"name": "zig", "distribution": "fakelang_zig-dist", "version": "1.0"}]}
        ),
        encoding="utf-8",
    )


def test_declared_grammar_passes(store: Path) -> None:
    _fake_library(store, "zig", b"zig-library")
    grammars.verify_declared(store, "zig", _spec())


def test_pack_version_mismatch_fails(store: Path) -> None:
    _fake_library(store, "zig", b"zig-library")
    with pytest.raises(
        grammars.GrammarError, match=r"宣言のパックの版 0\.0\.0 が、入っているパック"
    ):
        grammars.verify_declared(store, "zig", _spec(pack_version="0.0.0"))


def test_missing_platform_hash_fails(store: Path) -> None:
    _fake_library(store, "zig", b"zig-library")
    with pytest.raises(grammars.GrammarError, match=r"宣言に .* のハッシュがありません"):
        grammars.verify_declared(store, "zig", _spec(platform="plan9-riscv"))


def test_hash_mismatch_fails(store: Path) -> None:
    _fake_library(store, "zig", b"tampered")
    with pytest.raises(grammars.GrammarError, match="ハッシュが宣言と合いません"):
        grammars.verify_declared(store, "zig", _spec())


def test_missing_library_points_to_fetch(store: Path) -> None:
    with pytest.raises(grammars.GrammarError, match="jigane languages fetch"):
        grammars.verify_declared(store, "zig", _spec())


def test_odd_grammar_name_is_rejected(store: Path) -> None:
    with pytest.raises(grammars.GrammarError, match="英小文字"):
        grammars.verify_declared(store, "../*", _spec())


def test_declaration_naming_a_builtin_grammar_uses_the_builtin_record(
    store: Path, monkeypatch: pytest.MonkeyPatch, install: Installer, tmp_path: Path
) -> None:
    _fake_library(store, "python", b"genuine")
    monkeypatch.setattr(
        grammars, "_recorded", lambda: {grammars.platform_key(): {"python": _digest(b"genuine")}}
    )
    _enable_zig(install, tmp_path, _spec(b"other", pack_version="0"), grammar="python")
    (plugin,) = registry.discovered_plugins()
    target = grammars.external_target(plugin)
    assert target == grammars.BuiltinGrammar("python")
    target.verify(store)


def test_targets_list_builtins_then_external(install: Installer, tmp_path: Path) -> None:
    _enable_zig(install, tmp_path, _spec())
    listed = grammars.targets(registry.discovered_plugins())
    assert [(type(t), t.name) for t in listed] == [
        *((grammars.BuiltinGrammar, grammar) for grammar in grammars.GRAMMARS),
        (grammars.DeclaredGrammar, "zig"),
    ]


def test_builtin_verification_is_unchanged(store: Path) -> None:
    with pytest.raises(grammars.GrammarError, match="make setup"):
        grammars.verify(store, "python")
    with pytest.raises(grammars.GrammarError, match="未知の文法"):
        grammars.verify(store, "zig")
    assert grammars.targets() == tuple(grammars.BuiltinGrammar(g) for g in grammars.GRAMMARS)


def test_matching_external_grammar_leaves_the_language_ready(
    install: Installer, tmp_path: Path, store: Path
) -> None:
    _fake_library(store, "zig", b"zig-library")
    _enable_zig(install, tmp_path, _spec())
    assert registry.adapter_for("a.zig") is not None


def test_external_grammar_error_makes_the_language_unavailable(
    install: Installer, tmp_path: Path, store: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_library(store, "zig", b"tampered")
    _enable_zig(install, tmp_path, _spec())
    (state,) = [s for s in registry.states() if isinstance(s, LanguageUnavailable)]
    assert state.reason_kind is ReasonKind.GRAMMAR_ERROR
    assert "ハッシュが宣言と合いません" in state.detail
    assert state.distribution == "fakelang_zig-dist"
    # 使えない言語のファイルは、どのアダプタにも当たらず対応外になる
    assert registry.adapter_for("a.zig") is None
    assert commands.main(["languages", "list", "--json"]) == 0
    rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)["languages"]}
    assert rows["zig"]["state"] == "unavailable"
    assert rows["zig"]["reason_kind"] == "grammar_error"
    assert "ハッシュが宣言と合いません" in str(rows["zig"]["detail"])


_BUILTIN_CONTENTS = {name: name.encode() for name in grammars.GRAMMARS}


def _fake_download(
    store: Path, monkeypatch: pytest.MonkeyPatch, contents: dict[str, bytes]
) -> list[list[str]]:
    calls: list[list[str]] = []

    def download(names: list[str]) -> None:
        calls.append(list(names))
        for name in names:
            if name not in contents:
                raise RuntimeError(f"unknown grammar {name}")
            _fake_library(store, "c_sharp" if name == "csharp" else name, contents[name])

    monkeypatch.setattr(tree_sitter_language_pack, "download", download)
    recorded = {name: _digest(_BUILTIN_CONTENTS[name]) for name in grammars.GRAMMARS}
    monkeypatch.setattr(grammars, "_recorded", lambda: {grammars.platform_key(): recorded})
    return calls


def test_fetch_downloads_builtin_and_external_and_reports_success(
    install: Installer,
    tmp_path: Path,
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _fake_download(store, monkeypatch, {**_BUILTIN_CONTENTS, "zig": b"zig-library"})
    _enable_zig(install, tmp_path, _spec())
    assert commands.main(["languages", "fetch", "--accept-terms"]) == 0
    assert calls == [list(grammars.GRAMMARS), ["zig"]]
    assert capsys.readouterr().out == "取得して照合しました: python, java, swift, csharp, zig\n"
    assert registry.adapter_for("a.zig") is not None


def test_fetch_reports_a_failing_language_and_exits_nonzero(
    install: Installer,
    tmp_path: Path,
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _fake_download(store, monkeypatch, {**_BUILTIN_CONTENTS, "zig": b"tampered"})
    _enable_zig(install, tmp_path, _spec())
    assert commands.main(["languages", "fetch", "--accept-terms"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "取得して照合しました: python, java, swift, csharp\n"
    assert "言語 zig: 文法 zig のハッシュが宣言と合いません" in captured.err


def test_fetch_download_failure_of_one_language_is_reported(
    install: Installer,
    tmp_path: Path,
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _fake_download(store, monkeypatch, dict(_BUILTIN_CONTENTS))
    _enable_zig(install, tmp_path, _spec())
    assert commands.main(["languages", "fetch", "--accept-terms"]) == 2
    assert "言語 zig: 文法 zig を取得できません" in capsys.readouterr().err


def test_fetch_does_not_download_a_grammar_with_a_wrong_declaration(
    install: Installer,
    tmp_path: Path,
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _fake_download(store, monkeypatch, {**_BUILTIN_CONTENTS, "zig": b"zig-library"})
    _enable_zig(install, tmp_path, _spec(pack_version="0.0.0"))
    assert commands.main(["languages", "fetch", "--accept-terms"]) == 2
    assert calls == [list(grammars.GRAMMARS)]
    assert "宣言のパックの版 0.0.0" in capsys.readouterr().err


def test_fetch_builtin_mismatch_exits_nonzero(
    store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_download(store, monkeypatch, dict(_BUILTIN_CONTENTS))
    wrong = {name: _digest(b"x") for name in grammars.GRAMMARS}
    monkeypatch.setattr(grammars, "_recorded", lambda: {grammars.platform_key(): wrong})
    assert commands.main(["languages", "fetch", "--accept-terms"]) == 2
    assert "ハッシュが記録と合いません" in capsys.readouterr().err


def test_runtime_verification_uses_the_declaration_of_an_external_language(
    install: Installer, tmp_path: Path, store: Path
) -> None:
    _fake_library(store, "zig", b"tampered")
    _enable_zig(install, tmp_path, _spec())
    assert [s for s in registry.states() if isinstance(s, LanguageUnavailable)]
    # 使えない言語の宣言は呼ぶ側に渡らず、組み込みの記録に無い名前として止まる
    with pytest.raises(grammars.GrammarError, match=r"^未知の文法: zig$"):
        grammars.get_parser(grammars.BuiltinGrammar("zig"))
    _fake_library(store, "zig", b"zig-library")
    registry.set_states(None)
    adapter = registry.adapter_for("a.zig")
    assert adapter is not None
    target = grammar_target(adapter)
    assert isinstance(target, grammars.DeclaredGrammar)
    grammars._prepare(target)
    # 宣言と合わない実物に差し替わった後は、記録を捨てた照合で止まる
    _fake_library(store, "zig", b"swapped")
    grammars._checked.clear()
    with pytest.raises(grammars.GrammarError, match="ハッシュが宣言と合いません"):
        grammars._prepare(target)


@pytest.mark.usefixtures("store")
def test_fetch_failure_output_of_the_module_entry_is_sanitized(
    install: Installer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _enable_zig(install, tmp_path, _spec())
    failure = grammars.FetchFailure(
        grammars.DeclaredGrammar("zig", "zig\x1b[31m", _spec()),
        grammars.GrammarError("bad\x07 reason"),
    )
    monkeypatch.setattr(fetch_main, "fetch", lambda _external=(): [failure])
    assert fetch_main.main(["fetch"]) == 1
    err = capsys.readouterr().err
    assert "\x1b" not in err
    assert "\x07" not in err
    assert "言語 zig: bad reason" in err


def _stderr_of_entries(capsys: pytest.CaptureFixture[str]) -> tuple[str, str]:
    assert fetch_main.main(["fetch"]) == 1
    module_err = capsys.readouterr().err
    assert commands.main(["languages", "fetch", "--accept-terms"]) == 2
    return module_err, capsys.readouterr().err


def _stdout_of_command(capsys: pytest.CaptureFixture[str]) -> str:
    capsys.readouterr()
    commands.main(["languages", "fetch", "--accept-terms"])
    return capsys.readouterr().out


def test_both_fetch_entries_print_the_same_line_for_a_grammar_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def raise_grammar_error(_external: object = ()) -> list[grammars.FetchFailure]:
        raise grammars.GrammarError("bad\x07 grammar")

    monkeypatch.setattr(fetch_main, "fetch", raise_grammar_error)
    module_err, command_err = _stderr_of_entries(capsys)
    assert module_err == command_err == "jigane: bad grammar\n"
    assert _stdout_of_command(capsys) == ""


def test_both_fetch_entries_print_the_same_line_for_an_unavailable_plugin_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def raise_unavailable() -> list[object]:
        raise registry.LanguagePluginUnavailableError("enabled\x1b[31m broken")

    monkeypatch.setattr(registry, "discovered_plugins", raise_unavailable)
    module_err, command_err = _stderr_of_entries(capsys)
    assert module_err == command_err == "jigane: enabled broken\n"
    assert _stdout_of_command(capsys) == ""


@pytest.mark.usefixtures("store")
def test_both_fetch_entries_print_the_same_line_for_a_failing_external_language(
    install: Installer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _enable_zig(install, tmp_path, _spec())
    failure = grammars.FetchFailure(
        grammars.DeclaredGrammar("zig", "zig", _spec()), grammars.GrammarError("bad\x07 reason")
    )
    monkeypatch.setattr(fetch_main, "fetch", lambda _external=(): [failure])
    module_err, command_err = _stderr_of_entries(capsys)
    assert module_err == command_err == "jigane: 言語 zig: bad reason\n"
    out = _stdout_of_command(capsys)
    assert out.startswith("取得して照合しました: ")
    assert "zig" not in out


def test_registry_binds_the_declared_target_to_the_external_adapter(
    install: Installer, tmp_path: Path, store: Path
) -> None:
    _fake_library(store, "zig", b"zig-library")
    _enable_zig(install, tmp_path, _spec())
    adapter = registry.adapter_for("a.zig")
    assert adapter is not None
    assert adapter.grammar == "zig"
    assert grammar_target(adapter) == grammars.DeclaredGrammar("zig", "zig", _spec())


def test_builtin_adapter_target_is_the_builtin_record() -> None:
    adapter = registry.adapter_for("a.py")
    assert adapter is not None
    assert grammar_target(adapter) == grammars.BuiltinGrammar("python")


def test_grammar_bound_without_inner_raises_instead_of_recursing() -> None:
    bound = registry._GrammarBound.__new__(registry._GrammarBound)
    with pytest.raises(AttributeError):
        _ = bound.name


def test_load_queries_checks_the_grammar_by_the_declared_target(
    install: Installer, tmp_path: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _spec()
    data = tmp_path / "data"
    file = data / "languages" / "queries" / "zig.toml"
    file.parent.mkdir(parents=True)
    file.write_text('[[query]]\nname = "calls"\nquery = """(call) @call"""\n', encoding="utf-8")
    install("zig", suffixes=(".zig",), grammar="zig", spec_expr=_spec_expr(spec), data_dir=data)
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "languages.json").write_text(
        json.dumps(
            {"enabled": [{"name": "zig", "distribution": "fakelang_zig-dist", "version": "1.0"}]}
        ),
        encoding="utf-8",
    )
    _fake_library(store, "zig", b"zig-library")
    queries._load.cache_clear()
    asked: list[grammars.GrammarTarget] = []
    python_language = tree_sitter_language_pack.get_language("python")

    def _record(target: grammars.GrammarTarget) -> object:
        asked.append(target)
        return python_language

    monkeypatch.setattr(queries, "get_language", _record)
    assert "calls" in queries.load_queries("zig")
    queries._load.cache_clear()
    assert asked == [grammars.DeclaredGrammar("zig", "zig", spec)]


def test_grammar_target_named_falls_back_to_builtin_for_unknown_names() -> None:
    assert registry.grammar_target_named("python") == grammars.BuiltinGrammar("python")
    assert registry.grammar_target_named("nowhere") == grammars.BuiltinGrammar("nowhere")
