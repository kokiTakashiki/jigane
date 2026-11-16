import json
from pathlib import Path

import pytest
from language_plugin_support import Installer, approved, failure_of, states_of

from jigane import commands
from jigane.commands.language_guidance import guide_url
from jigane.languages import api
from jigane.languages.states import ReasonKind

TERM = "プラグイン API のバージョン"


def _enable(tmp_path: Path, *names: str) -> None:
    path = tmp_path / "home" / "languages.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [approved(name).to_json() for name in names]
    path.write_text(json.dumps({"enabled": records}), encoding="utf-8")


def _rows(capsys: pytest.CaptureFixture[str]) -> dict[str, dict[str, object]]:
    assert commands.main(["languages", "list", "--json"]) == 0
    return {row["name"]: row for row in json.loads(capsys.readouterr().out)["languages"]}


def test_old_major_is_api_version_old(install: Installer, tmp_path: Path) -> None:
    install("kotlin", major=1)
    failure = failure_of(states_of(tmp_path, ("kotlin",)), "kotlin")
    assert failure.reason_kind is ReasonKind.API_VERSION_OLD
    assert failure.import_failed is False
    assert failure.detail == (
        "言語プラグイン「kotlin」は、古いプラグイン API のバージョン（1.0）を宣言しています。"
        "この jigane のプラグイン API のバージョンは 2.0 です。"
        "プラグインを新しい API に合わせて直し、ApiVersion(2, 0) を宣言してください。"
    )


def test_newer_major_stays_a_validation_error(install: Installer, tmp_path: Path) -> None:
    install("kotlin", major=3)
    failure = failure_of(states_of(tmp_path, ("kotlin",)), "kotlin")
    assert failure.reason_kind is ReasonKind.VALIDATION_ERROR
    assert "契約の major が違います" in failure.detail


def test_import_failure_sets_import_failed(install: Installer, tmp_path: Path) -> None:
    install("kotlin", import_failure=True)
    failure = failure_of(states_of(tmp_path, ("kotlin",)), "kotlin")
    assert failure.reason_kind is ReasonKind.LOAD_ERROR
    assert failure.import_failed is True


def test_other_load_error_does_not_set_import_failed(install: Installer, tmp_path: Path) -> None:
    install("kotlin", raises=True)
    assert failure_of(states_of(tmp_path, ("kotlin",)), "kotlin").import_failed is False


def test_require_ready_stops_other_subcommands_with_the_guide(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("old", major=1)
    _enable(tmp_path, "old")
    assert commands.main(["list"]) != 0
    err = capsys.readouterr().err
    assert TERM in err
    assert f"直し方は {guide_url()} にあります。" in err
    assert "jigane languages disable old" in err


def test_list_row_and_json_report_api_version_old(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("old", major=1)
    install("fine", suffixes=(".fine",))
    _enable(tmp_path, "old", "fine")
    rows = _rows(capsys)
    assert rows["old"]["reason_kind"] == "api_version_old"
    assert TERM in str(rows["old"]["detail"])
    assert str(rows["old"]["detail"]).endswith(f"直し方は {guide_url()} にあります。")
    assert rows["fine"]["state"] == "ready"
    assert rows["fine"]["reason_kind"] is None
    assert commands.main(["languages", "list"]) == 0
    assert "理由（api_version_old）" in capsys.readouterr().out


def test_import_failed_language_gets_the_guide_line(
    install: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install("gone", import_failure=True)
    install("boom", raises=True)
    _enable(tmp_path, "gone", "boom")
    rows = _rows(capsys)
    line = f"言語プラグインの書き方と、0.1.9 での変更点は {guide_url()} にあります。"
    assert str(rows["gone"]["detail"]).endswith(line)
    assert line not in str(rows["boom"]["detail"])


def test_guide_url_points_at_the_renamed_section() -> None:
    assert guide_url().endswith("/docs/adding-languages.md#言語プラグインのインターフェースの改名")


def test_api_does_not_expose_the_version_constant() -> None:
    assert "PLUGIN_API_VERSION" not in api.__all__
    assert not hasattr(api, "PLUGIN_API_VERSION")
    assert "ApiVersion" in api.__all__
    assert api.ApiVersion(2, 0).major == 2
