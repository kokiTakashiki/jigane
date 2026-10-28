import json
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from language_plugin_support import (
    Installer,
    approved,
    enabled_of,
    entry_points_in,
    failure_of,
    legacy_record,
    states_of,
)

from jigane.languages import registry
from jigane.languages.plugins import (
    ApprovedContent,
    EnabledFileError,
    EnabledLanguage,
    FileDigest,
    LanguageNotEnabled,
    LanguageReady,
    LanguageUnavailable,
    LegacyApproval,
    ReasonKind,
    RecordError,
    discover,
    load_enabled,
    sanitize_text,
    save_enabled,
    validate_plugin,
)


def test_builtins_are_ready_without_entry_points() -> None:
    states = discover([], (), registry.builtin_plugins())
    assert [type(s) for s in states] == [LanguageReady] * 4


def test_not_enabled_entry_point_is_listed_but_never_imported(
    install: Installer, tmp_path: Path
) -> None:
    marker = install("kotlin")
    states = discover(entry_points_in(tmp_path), (), registry.builtin_plugins())
    assert LanguageNotEnabled("kotlin", "fakelang_kotlin-dist") in states
    assert not marker.exists()


def test_enabled_valid_plugin_is_ready(install: Installer, tmp_path: Path) -> None:
    marker = install("kotlin")
    state = states_of(tmp_path, ("kotlin",))["kotlin"]
    assert isinstance(state, LanguageReady)
    assert state.plugin.adapter.name == "kotlin"
    assert marker.exists()


def test_lower_minor_is_accepted(install: Installer, tmp_path: Path) -> None:
    install("kotlin", minor=0)
    assert isinstance(states_of(tmp_path, ("kotlin",))["kotlin"], LanguageReady)


def test_major_mismatch_fails(install: Installer, tmp_path: Path) -> None:
    install("kotlin", major=2)
    failure = failure_of(states_of(tmp_path, ("kotlin",)), "kotlin")
    assert failure.reason_kind is ReasonKind.VALIDATION_ERROR
    assert "major" in failure.detail


def test_newer_minor_fails(install: Installer, tmp_path: Path) -> None:
    install("kotlin", minor=1)
    failure = failure_of(states_of(tmp_path, ("kotlin",)), "kotlin")
    assert failure.reason_kind is ReasonKind.VALIDATION_ERROR
    assert "minor" in failure.detail


def test_adapter_missing_a_member_fails(install: Installer, tmp_path: Path) -> None:
    install("kotlin", complete=False)
    failure = failure_of(states_of(tmp_path, ("kotlin",)), "kotlin")
    assert failure.reason_kind is ReasonKind.VALIDATION_ERROR
    assert "LanguageAdapter" in failure.detail


def test_adapter_name_differing_from_entry_point_name_fails(
    install: Installer, tmp_path: Path
) -> None:
    install("kotlin", adapter_name="other")
    failure = failure_of(states_of(tmp_path, ("kotlin",)), "kotlin")
    assert failure.reason_kind is ReasonKind.VALIDATION_ERROR
    assert "アダプタの名前 other が entry point の名前と違います" in failure.detail


def test_origin_not_matching_the_distribution_fails(install: Installer, tmp_path: Path) -> None:
    install("kotlin", origin="Builtin()")
    failure = failure_of(states_of(tmp_path, ("kotlin",)), "kotlin")
    assert failure.reason_kind is ReasonKind.VALIDATION_ERROR
    assert "出どころ" in failure.detail


def test_suffix_overlap_between_externals_fails_both(install: Installer, tmp_path: Path) -> None:
    install("alpha", suffixes=(".x",))
    install("beta", suffixes=(".x", ".y"))
    states = states_of(tmp_path, ("alpha", "beta"))
    for name, other in (("alpha", "beta"), ("beta", "alpha")):
        failure = failure_of(states, name)
        assert failure.reason_kind is ReasonKind.VALIDATION_ERROR
        assert f"拡張子 .x が外の言語 {other} と重なります" in failure.detail


def test_suffix_overlap_with_a_builtin_is_not_an_error(install: Installer, tmp_path: Path) -> None:
    install("kotlin", suffixes=(".py", ".kt"))
    assert isinstance(states_of(tmp_path, ("kotlin",))["kotlin"], LanguageReady)


def test_overlap_with_a_disabled_external_is_not_an_error(
    install: Installer, tmp_path: Path
) -> None:
    install("alpha", suffixes=(".x",))
    install("beta", suffixes=(".x",))
    states = states_of(tmp_path, ("alpha",))
    assert isinstance(states["alpha"], LanguageReady)
    assert isinstance(states["beta"], LanguageNotEnabled)


def test_same_name_as_a_builtin_fails(install: Installer, tmp_path: Path) -> None:
    marker = install("python")
    states = discover(entry_points_in(tmp_path), enabled_of("python"), registry.builtin_plugins())
    failures = [s for s in states if isinstance(s, LanguageUnavailable)]
    assert [f.name for f in failures] == ["python"]
    assert failures[0].reason_kind is ReasonKind.VALIDATION_ERROR
    assert failures[0].detail == "組み込みの言語と同じ名前です"
    assert not marker.exists()


def test_load_exception_fails_with_load_error(install: Installer, tmp_path: Path) -> None:
    install("kotlin", raises=True)
    failure = failure_of(states_of(tmp_path, ("kotlin",)), "kotlin")
    assert failure.reason_kind is ReasonKind.LOAD_ERROR
    assert failure.detail == "RuntimeError: boom"


def test_enabled_name_without_entry_point_fails(tmp_path: Path) -> None:
    (tmp_path / "site").mkdir()
    states = discover([], [legacy_record("ghost")], registry.builtin_plugins())
    failure = failure_of(
        {s.name: s for s in states if isinstance(s, LanguageUnavailable)}, "ghost"
    )
    assert failure.reason_kind is ReasonKind.ENTRY_POINT_MISSING
    assert failure.detail == "対応する entry point がありません"


def test_one_failure_does_not_block_other_languages(install: Installer, tmp_path: Path) -> None:
    install("bad", raises=True)
    install("good", suffixes=(".good",))
    states = states_of(tmp_path, ("bad", "good"))
    assert isinstance(states["bad"], LanguageUnavailable)
    assert isinstance(states["good"], LanguageReady)


def test_enabled_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "data" / "languages.json"
    assert load_enabled(path) == ()
    enabled = (
        legacy_record("kotlin", "jigane-language-kotlin"),
        legacy_record("go", "g", "2"),
    )
    save_enabled(path, enabled)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "enabled": [
            {"name": "kotlin", "distribution": "jigane-language-kotlin", "version": "1.0"},
            {"name": "go", "distribution": "g", "version": "2"},
        ]
    }
    assert load_enabled(path) == enabled


def test_duplicate_enabled_pairs_are_collapsed(tmp_path: Path) -> None:
    path = tmp_path / "languages.json"
    path.write_text(
        json.dumps(
            {
                "enabled": [
                    {"name": "kotlin", "distribution": "Jigane_Language.Kotlin", "version": "1.0"},
                    {"name": "kotlin", "distribution": "jigane-language-kotlin", "version": "1.0"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert load_enabled(path) == (legacy_record("kotlin", "jigane-language-kotlin"),)


def test_duplicate_enabled_pairs_yield_one_state(install: Installer, tmp_path: Path) -> None:
    install("kotlin")
    pair = approved("kotlin")
    states = discover(entry_points_in(tmp_path), [pair, pair], registry.builtin_plugins())
    kotlin = [
        s
        for s in states
        if getattr(s, "name", None) == "kotlin"
        or (isinstance(s, LanguageReady) and s.plugin.adapter.name == "kotlin")
    ]
    assert [type(s) for s in kotlin] == [LanguageReady]


def test_distribution_names_are_compared_after_pep503_normalisation(
    install: Installer, tmp_path: Path
) -> None:
    install("kotlin", dist="Fake_Lang.Kotlin")
    enabled = (approved("kotlin", "fake-lang-kotlin"),)
    states = discover(entry_points_in(tmp_path), enabled, registry.builtin_plugins())
    assert any(isinstance(s, LanguageReady) and s.plugin.adapter.name == "kotlin" for s in states)


def test_same_name_from_another_distribution_is_not_imported(
    install: Installer, tmp_path: Path
) -> None:
    marker = install("kotlin", dist="evil-kotlin")
    enabled = (legacy_record("kotlin", "jigane-language-kotlin"),)
    states = discover(entry_points_in(tmp_path), enabled, registry.builtin_plugins())
    failures = [s for s in states if isinstance(s, LanguageUnavailable)]
    assert [f.name for f in failures] == ["kotlin"]
    assert failures[0].reason_kind is ReasonKind.VALIDATION_ERROR
    assert "有効にした配布と違う" in failures[0].detail
    assert "evil-kotlin" in failures[0].detail
    assert not marker.exists()


@pytest.mark.parametrize(
    "suffixes_expr",
    ["{'.fake'}", "['.fake']", "frozenset({'fake'})", "frozenset({1})", "None"],
)
def test_invalid_suffixes_fail_only_that_language(
    install: Installer, tmp_path: Path, suffixes_expr: str
) -> None:
    install("bad", suffixes_expr=suffixes_expr)
    install("good", suffixes=(".good",))
    states = states_of(tmp_path, ("bad", "good"))
    failure = failure_of(states, "bad")
    assert failure.reason_kind is ReasonKind.VALIDATION_ERROR
    assert "suffixes" in failure.detail
    assert isinstance(states["good"], LanguageReady)


@pytest.mark.parametrize("attribute", ["name", "grammar"])
def test_non_string_name_or_grammar_fails(
    install: Installer, tmp_path: Path, attribute: str
) -> None:
    install("bad")
    path = tmp_path / "site" / "fakelang_bad.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f"    {attribute} = ", f"    {attribute} = 1  # "
        ),
        encoding="utf-8",
    )
    failure = failure_of(states_of(tmp_path, ("bad",)), "bad")
    assert failure.reason_kind is ReasonKind.VALIDATION_ERROR
    assert f"アダプタの {attribute} が文字列ではありません" in failure.detail


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"enabled": "kotlin"}',
        '{"enabled": [1]}',
        '{"enabled": ["kotlin"]}',
        '{"enabled": [{"name": "kotlin"}]}',
        '{"enabled": [{"name": "kotlin", "distribution": 1, "version": "1"}]}',
        '{"enabled": [{"name": "k", "distribution": "d"}]}',
        '{"enabled": [], "extra": 1}',
        "{}",
    ],
)
def test_malformed_enabled_file_raises(tmp_path: Path, content: str) -> None:
    path = tmp_path / "languages.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(EnabledFileError):
        load_enabled(path)


def test_unreadable_enabled_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "languages.json"
    path.mkdir()
    with pytest.raises(EnabledFileError):
        load_enabled(path)


def test_sanitize_text_removes_terminal_controls_and_keeps_newline_and_tab() -> None:
    raw = "a\x1b[31mb\x1b]0;t\x07c\x08d\x9be\x7ff\tg\nh\x1bZi"
    assert sanitize_text(raw) == "abcdef\tg\nhi"


def test_enabled_language_json_round_trip() -> None:
    pair = legacy_record("kotlin", "Fake_Kotlin", "3.1")
    assert pair.to_json() == {"name": "kotlin", "distribution": "fake-kotlin", "version": "3.1"}
    assert EnabledLanguage.from_json(pair.to_json()) == pair
    with pytest.raises(ValueError, match="name と distribution と version と files だけ"):
        EnabledLanguage.from_json({"name": "kotlin"})
    with pytest.raises(ValueError, match="文字列"):
        EnabledLanguage.from_json({"name": 1, "distribution": "x", "version": "1"})


def test_enabled_language_with_files_json_round_trip() -> None:
    digest = "a" * 64
    content = ApprovedContent((FileDigest("pkg/a.py", digest), FileDigest("pkg/b.py", digest)))
    entry = EnabledLanguage("kotlin", "Fake_Kotlin", "3.1", content)
    assert entry.to_json()["files"] == {"pkg/a.py": digest, "pkg/b.py": digest}
    assert EnabledLanguage.from_json(entry.to_json()) == entry
    body = entry.to_json()
    for bad in ({"pkg/a.py": "xyz"}, {"pkg/a.py": 1}, ["pkg/a.py"]):
        with pytest.raises(ValueError, match="files は"):
            EnabledLanguage.from_json({**body, "files": bad})


def test_other_version_than_the_approved_one_is_not_imported(
    install: Installer, tmp_path: Path
) -> None:
    marker = install("kotlin")
    enabled = (legacy_record("kotlin", "fakelang_kotlin-dist", "0.9"),)
    states = discover(entry_points_in(tmp_path), enabled, registry.builtin_plugins())
    failures = [s for s in states if isinstance(s, LanguageUnavailable)]
    assert [f.name for f in failures] == ["kotlin"]
    assert failures[0].reason_kind is ReasonKind.VALIDATION_ERROR
    assert "承認した版と違う版" in failures[0].detail
    assert "fakelang_kotlin-dist 1.0" in failures[0].detail
    assert "jigane languages enable kotlin" in failures[0].detail
    assert not marker.exists()


def test_validate_plugin_rejects_a_value_that_is_not_a_plugin() -> None:
    entry_point = EntryPoint("kotlin", "fakelang_kotlin:make", "jigane.languages")
    assert validate_plugin(entry_point, object()) == "LanguagePlugin ではなく object を返しました"


def test_validate_plugin_rejects_an_entry_point_without_a_distribution() -> None:
    entry_point = EntryPoint("kotlin", "fakelang_kotlin:make", "jigane.languages")
    plugin = registry.builtin_plugins()[0]
    assert validate_plugin(entry_point, plugin) == "entry point の配布を特定できません"


def test_two_entry_points_of_the_approved_distribution_fail(
    install: Installer, tmp_path: Path
) -> None:
    install("kotlin")
    install("kotlin", dist="Fakelang.Kotlin-Dist")
    failure = failure_of(states_of(tmp_path, ("kotlin",)), "kotlin")
    assert failure.reason_kind is ReasonKind.VALIDATION_ERROR
    assert failure.detail == (
        "同じ名前の entry point が複数あります: Fakelang.Kotlin-Dist, fakelang_kotlin-dist"
    )


def _site_module(tmp_path: Path, name: str) -> Path:
    return tmp_path / "site" / f"fakelang_{name}.py"


def test_changed_content_with_the_same_version_is_not_imported(
    install: Installer, tmp_path: Path
) -> None:
    marker = install("kotlin")
    enabled = enabled_of("kotlin")
    with _site_module(tmp_path, "kotlin").open("a", encoding="utf-8") as module:
        module.write("# 差し替え\n")
    states = discover(entry_points_in(tmp_path), enabled, registry.builtin_plugins())
    failure = failure_of(
        {s.name: s for s in states if isinstance(s, LanguageUnavailable)}, "kotlin"
    )
    assert failure.reason_kind is ReasonKind.VALIDATION_ERROR
    assert "承認のあと、配布の中身が変わっています" in failure.detail
    assert "jigane languages enable kotlin" in failure.detail
    assert not marker.exists()


def test_missing_file_is_not_imported(install: Installer, tmp_path: Path) -> None:
    marker = install("kotlin")
    enabled = enabled_of("kotlin")
    _site_module(tmp_path, "kotlin").unlink()
    states = discover(entry_points_in(tmp_path), enabled, registry.builtin_plugins())
    failures = [s for s in states if isinstance(s, LanguageUnavailable)]
    assert [f.name for f in failures] == ["kotlin"]
    assert "承認のあと、配布の中身が変わっています" in failures[0].detail
    assert not marker.exists()


def test_added_file_is_not_imported(install: Installer, tmp_path: Path) -> None:
    marker = install("kotlin")
    enabled = enabled_of("kotlin")
    record = tmp_path / "site" / "fakelang_kotlin_dist-1.0.dist-info" / "RECORD"
    (tmp_path / "site" / "extra.py").write_text("", encoding="utf-8")
    record.write_text(record.read_text(encoding="utf-8") + "extra.py,,\n", encoding="utf-8")
    states = discover(entry_points_in(tmp_path), enabled, registry.builtin_plugins())
    failures = [s for s in states if isinstance(s, LanguageUnavailable)]
    assert [f.name for f in failures] == ["kotlin"]
    assert "承認のあと、配布の中身が変わっています" in failures[0].detail
    assert not marker.exists()


def test_approval_without_files_is_read_but_not_imported(
    install: Installer, tmp_path: Path
) -> None:
    marker = install("kotlin")
    path = tmp_path / "languages.json"
    path.write_text(
        json.dumps(
            {
                "enabled": [
                    {"name": "kotlin", "distribution": "fakelang_kotlin-dist", "version": "1.0"}
                ]
            }
        ),
        encoding="utf-8",
    )
    states = discover(entry_points_in(tmp_path), load_enabled(path), registry.builtin_plugins())
    failures = [s for s in states if isinstance(s, LanguageUnavailable)]
    assert [f.name for f in failures] == ["kotlin"]
    assert failures[0].reason_kind is ReasonKind.VALIDATION_ERROR
    assert "旧い形" in failures[0].detail
    assert "jigane languages enable kotlin" in failures[0].detail
    assert not marker.exists()


_STORED_FILE = """\
{
  "enabled": [
    {
      "name": "kotlin",
      "distribution": "fakelang-kotlin-dist",
      "version": "1.0",
      "files": {
        "fakelang_kotlin.py": "%(digest)s",
        "fakelang_kotlin_dist-1.0.dist-info/METADATA": "%(digest)s"
      }
    },
    {
      "name": "go",
      "distribution": "g",
      "version": "2"
    }
  ]
}
"""


def test_stored_file_is_written_back_unchanged(tmp_path: Path) -> None:
    # 保存の形（キーと値）は、中身を記録する型を足す前から変えない
    text = _STORED_FILE % {"digest": "a" * 64}
    source = tmp_path / "in.json"
    source.write_text(text, encoding="utf-8")
    loaded = load_enabled(source)
    assert [type(entry.content) for entry in loaded] == [ApprovedContent, LegacyApproval]
    target = tmp_path / "out.json"
    save_enabled(target, loaded)
    assert target.read_text(encoding="utf-8") == text


def test_discover_tells_current_legacy_and_changed_content_apart(
    install: Installer, tmp_path: Path
) -> None:
    install("kotlin")
    install("go", suffixes=(".go",))
    install("zig", suffixes=(".zig",))
    kotlin = approved("kotlin").to_json()
    changed = approved("zig").to_json()
    files = changed["files"]
    assert isinstance(files, dict)
    changed["files"] = dict.fromkeys(files, "0" * 64)
    legacy = {"name": "go", "distribution": "fakelang-go-dist", "version": "1.0"}
    path = tmp_path / "languages.json"
    path.write_text(json.dumps({"enabled": [kotlin, legacy, changed]}), encoding="utf-8")
    states = discover(entry_points_in(tmp_path), load_enabled(path), registry.builtin_plugins())
    by_name = {
        (s.plugin.adapter.name if isinstance(s, LanguageReady) else s.name): s for s in states
    }
    assert isinstance(by_name["kotlin"], LanguageReady)
    assert "旧い形" in failure_of(by_name, "go").detail
    assert "承認のあと、配布の中身が変わっています" in failure_of(by_name, "zig").detail


def test_distribution_without_record_cannot_be_approved(
    install: Installer, tmp_path: Path
) -> None:
    install("kotlin")
    (tmp_path / "site" / "fakelang_kotlin_dist-1.0.dist-info" / "RECORD").unlink()
    with pytest.raises(RecordError, match="RECORD が無く"):
        approved("kotlin")


def test_matching_content_is_imported(install: Installer, tmp_path: Path) -> None:
    marker = install("kotlin")
    # 承認の後に pyc ができても、中身は変わらない
    pycache = tmp_path / "site" / "__pycache__"
    enabled = enabled_of("kotlin")
    pycache.mkdir()
    (pycache / "fakelang_kotlin.cpython-312.pyc").write_bytes(b"x")
    record = tmp_path / "site" / "fakelang_kotlin_dist-1.0.dist-info" / "RECORD"
    record.write_text(
        record.read_text(encoding="utf-8") + "__pycache__/fakelang_kotlin.cpython-312.pyc,,\n",
        encoding="utf-8",
    )
    states = discover(entry_points_in(tmp_path), enabled, registry.builtin_plugins())
    assert any(isinstance(s, LanguageReady) and s.plugin.adapter.name == "kotlin" for s in states)
    assert marker.exists()
