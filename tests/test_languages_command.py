import io
import json
from pathlib import Path

import pytest
from language_plugin_support import Installer

from jigane import commands
from jigane.languages import registry
from jigane.skills.policy_figures import REGISTRY, LanguageAgnostic

NOTICE_TRUST = (
    "外の言語のコードと文法は、jigane と同じ権限で動きます。"
    "出どころを信頼できる配布だけを有効にしてください。"
)
NOTICE_GRAMMAR = "文法を取得してください: jigane languages fetch"


def _languages_file(tmp_path: Path) -> Path:
    return tmp_path / "home" / "languages.json"


def _write_enabled(tmp_path: Path, *pairs: tuple[str, str]) -> None:
    path = _languages_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [{"name": name, "distribution": dist, "version": "1.0"} for name, dist in pairs]
    path.write_text(json.dumps({"enabled": entries}), encoding="utf-8")


def _enable(argv: list[str], answer: str = "y", *, terminal: bool = True) -> int:
    def refuse(_prompt: str) -> str:
        raise AssertionError("端末でないのに質問した")

    return commands.main(
        argv,
        is_terminal=lambda: terminal,
        read_answer=(lambda _prompt: answer) if terminal else refuse,
    )


def _rows(capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, dict[str, object]]:
    assert commands.main(["languages", "list", "--json", *argv]) == 0
    languages = json.loads(capsys.readouterr().out)["languages"]
    return {row["name"]: row for row in languages}


def test_list_shows_builtins_and_external_states(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("ready")
    install("idle", suffixes=(".idle",))
    install("broken", raises=True)
    _write_enabled(tmp_path, ("ready", "fakelang_ready-dist"), ("broken", "fakelang_broken-dist"))
    assert commands.main(["languages", "list"]) == 0
    out = capsys.readouterr().out
    for builtin in ("python", "java", "swift", "csharp"):
        assert builtin in out
    assert "組み込み" in out
    ready_line = next(line for line in out.splitlines() if line.startswith("ready\t"))
    assert "fakelang_ready-dist 1.0" in ready_line
    assert "使える" in ready_line
    assert ".fake" in ready_line
    idle_line = next(line for line in out.splitlines() if line.startswith("idle\t"))
    assert "有効でない" in idle_line
    assert "fakelang_idle-dist 1.0" in idle_line
    broken_line = next(line for line in out.splitlines() if line.startswith("broken\t"))
    assert "使えない" in broken_line
    assert "load_error" in out
    assert "boom" in out


def test_list_json_shape(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("ready")
    install("idle")
    _write_enabled(tmp_path, ("ready", "fakelang_ready-dist"))
    rows = _rows(capsys)
    assert rows["python"]["origin"] == "builtin"
    assert rows["python"]["state"] == "ready"
    assert rows["python"]["distribution"] is None
    assert ".py" in rows["python"]["suffixes"]  # type: ignore[operator]
    ready = {k: v for k, v in rows["ready"].items() if k not in ("interfaces", "figures")}
    assert ready == {
        "name": "ready",
        "origin": "distribution",
        "distribution": "fakelang_ready-dist",
        "version": "1.0",
        "state": "ready",
        "suffixes": [".fake"],
        "reason_kind": None,
        "detail": None,
    }
    assert rows["idle"]["state"] == "not_enabled"
    assert rows["idle"]["version"] == "1.0"
    assert rows["idle"]["suffixes"] == []


def test_list_json_reports_unavailable_reason(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("broken", raises=True)
    _write_enabled(tmp_path, ("broken", "fakelang_broken-dist"))
    row = _rows(capsys)["broken"]
    assert row["state"] == "unavailable"
    assert row["reason_kind"] == "load_error"
    assert "boom" in str(row["detail"])
    assert row["distribution"] == "fakelang_broken-dist"


def test_list_does_not_import_a_disabled_language(
    install: Installer, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = install("idle")
    assert _rows(capsys)["idle"]["state"] == "not_enabled"
    assert not marker.exists()


def test_enable_writes_after_validation_and_prints_notices(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("zig", suffixes=(".zig",))
    assert _enable(["languages", "enable", "zig"]) == 0
    captured = capsys.readouterr()
    assert NOTICE_TRUST in captured.err
    assert "fakelang-zig-dist 1.0" in captured.err
    assert NOTICE_GRAMMAR in captured.out
    saved = json.loads(_languages_file(tmp_path).read_text(encoding="utf-8"))
    assert saved == {
        "enabled": [{"name": "zig", "distribution": "fakelang-zig-dist", "version": "1.0"}]
    }


def test_enable_does_not_write_when_validation_fails(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("bad", raises=True)
    install("mismatch", adapter_name="other")
    assert _enable(["languages", "enable", "bad"]) != 0
    assert "boom" in capsys.readouterr().err
    assert _enable(["languages", "enable", "mismatch"]) != 0
    assert "entry point の名前と違います" in capsys.readouterr().err
    assert not _languages_file(tmp_path).exists()


def test_enable_rejects_unknown_and_builtin_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _enable(["languages", "enable", "nothing"]) != 0
    assert "nothing" in capsys.readouterr().err
    assert _enable(["languages", "enable", "python"]) != 0
    assert "組み込み" in capsys.readouterr().err
    assert not _languages_file(tmp_path).exists()


def test_enable_requires_distribution_when_several_provide_the_name(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("zig", dist="first-dist")
    install("zig", dist="second-dist")
    assert _enable(["languages", "enable", "zig"]) != 0
    err = capsys.readouterr().err
    assert "first-dist" in err
    assert "second-dist" in err
    assert "--distribution" in err
    assert not _languages_file(tmp_path).exists()
    assert _enable(["languages", "enable", "zig", "--distribution", "Second_Dist"]) == 0
    saved = json.loads(_languages_file(tmp_path).read_text(encoding="utf-8"))
    assert saved == {"enabled": [{"name": "zig", "distribution": "second-dist", "version": "1.0"}]}


def test_enable_keeps_existing_entries(install: Installer, tmp_path: Path) -> None:
    install("zig")
    _write_enabled(tmp_path, ("other", "other-dist"))
    assert _enable(["languages", "enable", "zig"]) == 0
    saved = json.loads(_languages_file(tmp_path).read_text(encoding="utf-8"))
    assert [entry["name"] for entry in saved["enabled"]] == ["other", "zig"]


def test_disable_removes_the_pair(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_enabled(tmp_path, ("zig", "zig-dist"), ("go", "go-dist"))
    assert commands.main(["languages", "disable", "zig"]) == 0
    assert "外しました" in capsys.readouterr().out
    saved = json.loads(_languages_file(tmp_path).read_text(encoding="utf-8"))
    assert saved == {"enabled": [{"name": "go", "distribution": "go-dist", "version": "1.0"}]}


def test_disable_of_an_absent_name_is_not_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert commands.main(["languages", "disable", "zig"]) == 0
    assert "有効にしていません" in capsys.readouterr().out
    assert not _languages_file(tmp_path).exists()


def test_disable_with_a_broken_file_guides_and_keeps_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _languages_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert commands.main(["languages", "disable", "zig"]) != 0
    assert "languages.json を直すか、消して" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == "{not json"


def test_languages_commands_work_while_an_external_language_is_unavailable(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("broken", raises=True)
    _write_enabled(tmp_path, ("broken", "fakelang_broken-dist"))
    assert commands.main(["list"]) != 0
    assert "jigane languages disable broken" in capsys.readouterr().err
    assert _rows(capsys)["broken"]["state"] == "unavailable"
    assert commands.main(["languages", "disable", "broken"]) == 0
    capsys.readouterr()
    registry.set_states(None)
    assert commands.main(["list"]) == 0


def test_enable_again_does_not_import_or_duplicate(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("zig")
    _write_enabled(tmp_path, ("zig", "fakelang-zig-dist"))
    # 端末でなくても通る: 確認も、検証のための読み込みも行わない
    assert _enable(["languages", "enable", "zig"], terminal=False) == 0
    assert "既に有効" in capsys.readouterr().out
    saved = json.loads(_languages_file(tmp_path).read_text(encoding="utf-8"))
    assert saved == {
        "enabled": [{"name": "zig", "distribution": "fakelang-zig-dist", "version": "1.0"}]
    }


def test_enable_adds_another_distribution_with_the_same_name(
    install: Installer, tmp_path: Path
) -> None:
    install("zig", dist="first-dist")
    install("zig", dist="second-dist")
    _write_enabled(tmp_path, ("zig", "first-dist"))
    assert _enable(["languages", "enable", "zig", "--distribution", "second-dist"]) == 0
    saved = json.loads(_languages_file(tmp_path).read_text(encoding="utf-8"))
    assert [entry["distribution"] for entry in saved["enabled"]] == ["first-dist", "second-dist"]


def test_enable_after_an_upgrade_asks_again_and_replaces_the_old_version(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("zig")
    path = _languages_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = {"name": "zig", "distribution": "fakelang-zig-dist", "version": "0.9"}
    path.write_text(json.dumps({"enabled": [old]}), encoding="utf-8")
    # 版が違う承認は有効な承認に数えず、端末でなければ確認を得られず止まる
    assert _enable(["languages", "enable", "zig"], terminal=False) != 0
    assert json.loads(path.read_text(encoding="utf-8")) == {"enabled": [old]}
    capsys.readouterr()
    assert _enable(["languages", "enable", "zig"]) == 0
    assert "fakelang-zig-dist 1.0" in capsys.readouterr().err
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {"enabled": [{**old, "version": "1.0"}]}


@pytest.mark.parametrize("answer", ["n", "", "no", "yes please"])
def test_enable_declined_does_not_import_or_write(
    install: Installer, tmp_path: Path, answer: str
) -> None:
    marker = install("zig")
    assert _enable(["languages", "enable", "zig"], answer) != 0
    assert not marker.exists()
    assert not _languages_file(tmp_path).exists()


@pytest.mark.usefixtures("real_read_answer")
def test_enable_eof_at_the_question_is_a_refusal(
    install: Installer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = install("zig")
    declined = _enable(["languages", "enable", "zig"], "n")
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code = commands.main(["languages", "enable", "zig"], is_terminal=lambda: True)
    assert code == declined
    assert code != 0
    assert "Traceback" not in capsys.readouterr().err
    assert not marker.exists()
    assert not _languages_file(tmp_path).exists()


def test_enable_without_a_terminal_does_not_import_or_write(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = install("zig")
    assert _enable(["languages", "enable", "zig"], terminal=False) != 0
    assert "端末" in capsys.readouterr().err
    assert not marker.exists()
    assert not _languages_file(tmp_path).exists()


@pytest.mark.parametrize("answer", ["y", "YES", " yes "])
def test_enable_accepts_yes_answers(install: Installer, answer: str) -> None:
    marker = install("zig")
    assert _enable(["languages", "enable", "zig"], answer) == 0
    assert marker.exists()


def test_disable_requires_distribution_when_several_are_enabled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_enabled(tmp_path, ("zig", "first-dist"), ("zig", "second-dist"), ("go", "go-dist"))
    assert commands.main(["languages", "disable", "zig"]) != 0
    err = capsys.readouterr().err
    assert "first-dist" in err
    assert "second-dist" in err
    assert "--distribution" in err
    assert len(json.loads(_languages_file(tmp_path).read_text(encoding="utf-8"))["enabled"]) == 3
    assert commands.main(["languages", "disable", "zig", "--distribution", "First_Dist"]) == 0
    saved = json.loads(_languages_file(tmp_path).read_text(encoding="utf-8"))
    assert saved["enabled"] == [
        {"name": "zig", "distribution": "second-dist", "version": "1.0"},
        {"name": "go", "distribution": "go-dist", "version": "1.0"},
    ]


def test_disable_with_an_unmatched_distribution_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_enabled(tmp_path, ("zig", "zig-dist"))
    assert commands.main(["languages", "disable", "zig", "--distribution", "other"]) == 0
    assert "有効にしていません" in capsys.readouterr().out
    assert len(json.loads(_languages_file(tmp_path).read_text(encoding="utf-8"))["enabled"]) == 1


def test_unavailable_row_names_the_approved_distribution_as_owner(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("zig", dist="other-dist")
    install("zig", dist="owner-dist", raises=True)
    _write_enabled(tmp_path, ("zig", "owner-dist"))
    row = _rows(capsys)["zig"]
    assert row["state"] == "unavailable"
    assert row["distribution"] == "owner-dist"
    assert row["version"] == "1.0"


def test_unavailable_row_without_entry_point_uses_the_recorded_distribution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_enabled(tmp_path, ("ghost", "ghost-dist"))
    row = _rows(capsys)["ghost"]
    assert row["reason_kind"] == "entry_point_missing"
    assert row["distribution"] == "ghost-dist"
    assert row["version"] is None


def test_control_characters_in_exception_text_are_removed(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module_dir = tmp_path / "site"
    install("evil", raises=True)
    source = next(module_dir.glob("fakelang_evil.py"))
    text = source.read_text(encoding="utf-8")
    source.write_text(
        text.replace('"boom"', r'"\x1b[31mred\x1b]0;title\x07\x08\x9bX\nnext"'),
        encoding="utf-8",
    )
    if "red" not in source.read_text(encoding="utf-8"):
        pytest.fail("偽の配布の例外の文を差し替えられなかった")
    _write_enabled(tmp_path, ("evil", "fakelang_evil-dist"))
    assert commands.main(["languages", "list"]) == 0
    out = capsys.readouterr().out
    assert "red" in out
    for forbidden in ("\x1b", "\x07", "\x08", "\x9b"):
        assert forbidden not in out
    assert "\nnext" in out
    assert commands.main(["list"]) != 0
    err = capsys.readouterr().err
    assert "red" in err
    assert "\x1b" not in err


def test_json_shape_of_unavailable_row_is_unchanged(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("broken", raises=True)
    _write_enabled(tmp_path, ("broken", "fakelang_broken-dist"))
    row = _rows(capsys)["broken"]
    assert list(row) == [
        "name",
        "origin",
        "distribution",
        "version",
        "state",
        "suffixes",
        "reason_kind",
        "detail",
    ]
    assert row["origin"] == "distribution"
    assert row["suffixes"] == []


_COMMON = {"SequenceAdapter", "ConcurrencyAdapter", "ImportAdapter", "RegexAdapter"}
_EXTRA = {
    "python": {"CommandArgumentAdapter", "FilePathAdapter", "StoreAccessReader"},
    "csharp": {"MarkupBindingAdapter"},
    "java": set(),
    "swift": set(),
}
_ALL_INTERFACES = {
    "SequenceAdapter",
    "ConcurrencyAdapter",
    "ImportAdapter",
    "MarkupBindingAdapter",
    "CommandArgumentAdapter",
    "FilePathAdapter",
    "StoreAccessReader",
    "RegexAdapter",
}


def test_builtin_interfaces_match_the_implementation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = _rows(capsys)
    for name, extra in _EXTRA.items():
        interfaces = rows[name]["interfaces"]
        assert isinstance(interfaces, dict)
        assert set(interfaces) == _ALL_INTERFACES
        assert {key for key, value in interfaces.items() if value} == _COMMON | extra


def test_builtin_figures_follow_the_figure_interfaces(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = _rows(capsys)
    language_figures = [
        (f.key, f.interface.__name__)
        for f in REGISTRY
        if not isinstance(f.interface, LanguageAgnostic)
    ]
    for name, extra in _EXTRA.items():
        figures = rows[name]["figures"]
        assert isinstance(figures, dict)
        assert set(figures) == {key for key, _ in language_figures}
        expected = {key for key, interface in language_figures if interface in _COMMON | extra}
        assert {key for key, value in figures.items() if value} == expected


def test_declarations_only_external_language_supports_no_language_figure(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("ready")
    install("idle")
    _write_enabled(tmp_path, ("ready", "fakelang_ready-dist"))
    rows = _rows(capsys)
    interfaces = rows["ready"]["interfaces"]
    figures = rows["ready"]["figures"]
    assert isinstance(interfaces, dict)
    assert isinstance(figures, dict)
    assert not any(interfaces.values())
    assert figures
    assert not any(figures.values())
    assert "interfaces" not in rows["idle"]
    assert "figures" not in rows["idle"]


def test_table_lists_supported_and_unsupported_figures_with_one_notice(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("ready")
    _write_enabled(tmp_path, ("ready", "fakelang_ready-dist"))
    assert commands.main(["languages", "list"]) == 0
    out = capsys.readouterr().out
    assert out.count("口は試験的です。jigane の版で変わることがあります") == 1
    ready_count = len(registry.adapters())
    assert out.count("対応する図:") == ready_count
    assert out.count("対応外の図:") == ready_count
