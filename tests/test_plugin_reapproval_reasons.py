import re
from pathlib import Path

import pytest
from language_plugin_support import Installer, approved, entry_points_in, legacy_record

from jigane.languages import registry
from jigane.languages.plugins import (
    ApprovedContent,
    EnabledLanguage,
    FileDigest,
    LanguageUnavailable,
    discover,
    reapproval_reason,
)

_A = "a" * 64
_B = "b" * 64


def _content(files: dict[str, str]) -> ApprovedContent:
    return ApprovedContent(
        tuple(FileDigest(path, digest) for path, digest in sorted(files.items()))
    )


def test_reason_names_changed_missing_and_added_files_by_kind() -> None:
    reason = reapproval_reason(
        [_content({"keep.py": _A, "edit.py": _A, "gone.py": _A})],
        {"keep.py": _A, "edit.py": _B, "new.py": _A},
    )
    assert reason == (
        "承認のあと、配布の中身が変わっています"
        "（変わった: edit.py／欠けた: gone.py／増えた: new.py）"
    )


def test_reason_counts_a_file_absent_from_the_disk_as_missing() -> None:
    reason = reapproval_reason([_content({"a.py": _A})], {"a.py": _A}, absent=["lost.py"])
    assert "欠けた: lost.py" in reason


def test_reason_lists_at_most_five_paths_and_counts_the_rest() -> None:
    actual = {f"f{index}.py": _B for index in range(8)}
    reason = reapproval_reason([_content(dict.fromkeys(actual, _A))], actual)
    assert reason.count(".py") == 5
    assert "ほか 3 件" in reason


def test_reason_with_exactly_five_paths_has_no_remainder() -> None:
    actual = {f"f{index}.py": _B for index in range(5)}
    assert "ほか" not in reapproval_reason([_content(dict.fromkeys(actual, _A))], actual)


def test_reason_never_shows_a_hash() -> None:
    reason = reapproval_reason([_content({"a.py": _A})], {"a.py": _B})
    assert not re.search(r"[0-9a-f]{16}", reason)


def test_reason_drops_control_characters_from_paths() -> None:
    reason = reapproval_reason([_content({"a.py": _A})], {"a.py": _A, "x\x1b[31m\x07y.py": _A})
    assert "\x1b" not in reason
    assert "\x07" not in reason
    assert "増えた: xy.py" in reason


def test_reason_compares_with_the_approval_that_differs_least() -> None:
    far = _content({"p.py": _A, "q.py": _A, "r.py": _A, "s.py": _A, "t.py": _A})
    near = _content({"p.py": _A, "q.py": _B, "r.py": _A})
    reason = reapproval_reason([far, near], {"p.py": _A, "q.py": _A, "r.py": _A})
    assert "変わった: q.py" in reason
    assert "s.py" not in reason


def test_reason_for_a_record_in_the_old_form_says_it_cannot_be_compared() -> None:
    legacy = legacy_record("zig").content
    assert reapproval_reason([legacy], {"a.py": _A}) == (
        "旧い形の承認記録です（中身の照合ができません）"
    )


def _detail(tmp_path: Path, enabled: tuple[EnabledLanguage, ...]) -> str:
    states = discover(entry_points_in(tmp_path), enabled, registry.builtin_plugins())
    (failure,) = [s for s in states if isinstance(s, LanguageUnavailable)]
    return failure.detail


def test_discover_shows_the_changed_file_and_the_reapproval_command(
    install: Installer, tmp_path: Path
) -> None:
    install("kotlin")
    enabled = (approved("kotlin"),)
    with (tmp_path / "site" / "fakelang_kotlin.py").open("a", encoding="utf-8") as module:
        module.write("# 差し替え\n")
    assert _detail(tmp_path, enabled) == (
        "承認のあと、配布の中身が変わっています（変わった: fakelang_kotlin.py）。"
        "jigane languages enable kotlin で承認し直してください"
    )


def test_discover_counts_a_deleted_file_as_missing(install: Installer, tmp_path: Path) -> None:
    install("kotlin")
    enabled = (approved("kotlin"),)
    (tmp_path / "site" / "fakelang_kotlin.py").unlink()
    assert "欠けた: fakelang_kotlin.py" in _detail(tmp_path, enabled)


def test_discover_counts_a_new_record_line_as_added(install: Installer, tmp_path: Path) -> None:
    install("kotlin")
    enabled = (approved("kotlin"),)
    record = tmp_path / "site" / "fakelang_kotlin_dist-1.0.dist-info" / "RECORD"
    (tmp_path / "site" / "extra.py").write_text("", encoding="utf-8")
    record.write_text(record.read_text(encoding="utf-8") + "extra.py,,\n", encoding="utf-8")
    assert "増えた: extra.py" in _detail(tmp_path, enabled)


def test_discover_says_a_record_in_the_old_form_cannot_be_compared(
    install: Installer, tmp_path: Path
) -> None:
    install("kotlin")
    assert _detail(tmp_path, (legacy_record("kotlin"),)) == (
        "旧い形の承認記録です（中身の照合ができません）。"
        "jigane languages enable kotlin で承認し直してください"
    )


def test_discover_keeps_the_unreadable_file_reason(
    install: Installer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install("kotlin")
    enabled = (approved("kotlin"),)
    module = tmp_path / "site" / "fakelang_kotlin.py"
    original = Path.read_bytes

    def read_bytes(self: Path) -> bytes:
        if self == module:
            raise PermissionError(13, "Permission denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    detail = _detail(tmp_path, enabled)
    assert "RECORD に載る fakelang_kotlin.py を読めません" in detail


def test_old_form_record_gets_the_old_form_reason_even_without_a_record_file(
    install: Installer, tmp_path: Path
) -> None:
    install("kotlin")
    (tmp_path / "site" / "fakelang_kotlin_dist-1.0.dist-info" / "RECORD").unlink()
    assert _detail(tmp_path, (legacy_record("kotlin"),)) == (
        "旧い形の承認記録です（中身の照合ができません）。"
        "jigane languages enable kotlin で承認し直してください"
    )
