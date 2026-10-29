"""SARIF の型、上限、文字列の除去、識別子の連番の試験。"""

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sarif_support import log, result, run, write

from jigane.static_analysis.models import SarifImport, StaticFinding
from jigane.static_analysis.reader import DEFAULT_LIMITS, SarifError, SarifLimits, read_sarif


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def read_one(
    repository: Path, document: object, limits: SarifLimits = DEFAULT_LIMITS
) -> SarifImport:
    path = write(repository.parent, "one.sarif", document)
    return read_sarif([path], repository, repository, limits)


def only(repository: Path, *results: dict[str, Any], **run_arguments: object) -> StaticFinding:
    imported = read_one(repository, log(run(list(results), **run_arguments)))
    assert len(imported.findings) == 1
    return imported.findings[0]


def test_normalizes_one_finding(repository: Path) -> None:
    finding = only(
        repository,
        result("src/a.py", 3, "bad", ruleId="E501", level="error"),
    )
    assert finding == StaticFinding(
        id="S1",
        tool_name="ruff",
        tool_version="1.0",
        rule_id="E501",
        level="error",
        message="bad",
        path="src/a.py",
        start_line=3,
        end_line=3,
    )


def test_version_other_than_2_1_0_is_rejected(repository: Path) -> None:
    with pytest.raises(SarifError, match="version"):
        read_one(repository, log(run([result()]), version="2.0.0"))


def test_missing_driver_name_is_rejected(repository: Path) -> None:
    document = {"version": "2.1.0", "runs": [{"tool": {"driver": {}}, "results": [result()]}]}
    with pytest.raises(SarifError, match=r"runs\.0\.tool\.driver\.name"):
        read_one(repository, document)


def test_missing_message_text_is_rejected(repository: Path) -> None:
    broken = result()
    broken["message"] = {}
    with pytest.raises(SarifError, match=r"results\.0\.message\.text"):
        read_one(repository, log(run([broken])))


def test_string_line_number_is_rejected_not_converted(repository: Path) -> None:
    with pytest.raises(SarifError, match="startLine"):
        read_one(repository, log(run([result(start_line="3")])))


def test_boolean_and_float_line_numbers_are_rejected(repository: Path) -> None:
    for value in (True, 3.0):
        with pytest.raises(SarifError, match="startLine"):
            read_one(repository, log(run([result(start_line=value)])))


def test_wrong_type_of_used_field_names_the_file(repository: Path) -> None:
    path = write(repository.parent, "bad.sarif", log(run([result(ruleId=5)])))
    with pytest.raises(SarifError, match=r"bad\.sarif") as raised:
        read_sarif([path], repository, repository)
    assert "ruleId" in str(raised.value)


def test_unknown_fields_are_ignored(repository: Path) -> None:
    document = log(run([result(extra_field={"x": [1]}, fixes=5)], properties={"a": 1}))
    document["$schema"] = "https://example.invalid/sarif"
    assert len(read_one(repository, document).findings) == 1


def test_level_defaults_to_warning(repository: Path) -> None:
    assert only(repository, result()).level == "warning"


def test_unknown_level_is_rejected(repository: Path) -> None:
    with pytest.raises(SarifError, match="level"):
        read_one(repository, log(run([result(level="fatal")])))


def test_end_line_defaults_to_start_line(repository: Path) -> None:
    finding = only(repository, result(start_line=7))
    assert (finding.start_line, finding.end_line) == (7, 7)


def test_end_line_is_read_when_present(repository: Path) -> None:
    body = result(start_line=7)
    body["locations"][0]["physicalLocation"]["region"]["endLine"] = 9
    assert only(repository, body).end_line == 9


def test_missing_rule_id_and_tool_version_become_empty(repository: Path) -> None:
    finding = only(repository, result(), version=None)
    assert (finding.rule_id, finding.tool_version) == ("", "")


def test_result_without_location_is_dropped_and_counted(repository: Path) -> None:
    imported = read_one(repository, log(run([result(uri=None), result(start_line=None)])))
    assert imported.findings == ()
    assert {reason.value: count for reason, count in imported.dropped.items()} == {
        "no_location": 2
    }


def test_second_location_is_not_used_or_validated(repository: Path) -> None:
    body = result()
    body["locations"].append("not even an object")
    assert only(repository, body).path == "src/a.py"


def test_invalid_json_is_rejected(repository: Path) -> None:
    path = repository.parent / "broken.sarif"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SarifError, match=r"broken\.sarif"):
        read_sarif([path], repository, repository)


def test_non_utf8_bytes_are_rejected(repository: Path) -> None:
    path = repository.parent / "binary.sarif"
    path.write_bytes(b"\xff\xfe\x00{")
    with pytest.raises(SarifError, match=r"binary\.sarif"):
        read_sarif([path], repository, repository)


def test_deeply_nested_json_is_rejected(repository: Path) -> None:
    path = repository.parent / "deep.sarif"
    path.write_text("[" * 200_000 + "]" * 200_000, encoding="utf-8")
    with pytest.raises(SarifError, match="入れ子"):
        read_sarif([path], repository, repository)


def test_top_level_array_is_rejected(repository: Path) -> None:
    with pytest.raises(SarifError):
        read_one(repository, [1, 2])


def test_file_over_size_limit_is_rejected_before_reading(repository: Path) -> None:
    path = repository.parent / "huge.sarif"
    with path.open("wb") as stream:
        stream.truncate(DEFAULT_LIMITS.max_file_bytes + 1)
    with pytest.raises(SarifError, match=r"huge\.sarif"):
        read_sarif([path], repository, repository)


def test_file_at_size_limit_is_read(repository: Path) -> None:
    path = write(repository.parent, "a.sarif", log(run([result()])))
    size = path.stat().st_size
    imported = read_sarif(
        [path], repository, repository, replace(DEFAULT_LIMITS, max_file_bytes=size)
    )
    assert len(imported.findings) == 1
    with pytest.raises(SarifError):
        read_sarif(
            [path], repository, repository, replace(DEFAULT_LIMITS, max_file_bytes=size - 1)
        )


def test_twenty_one_files_are_rejected(repository: Path) -> None:
    paths = [write(repository.parent, f"{n}.sarif", log(run([]))) for n in range(21)]
    assert len(read_sarif(paths[:20], repository, repository).files) == 20
    with pytest.raises(SarifError, match="20"):
        read_sarif(paths, repository, repository)


def test_total_size_over_limit_is_rejected(repository: Path) -> None:
    paths = [write(repository.parent, f"{n}.sarif", log(run([result()]))) for n in range(3)]
    each = paths[0].stat().st_size
    limits = replace(DEFAULT_LIMITS, max_total_bytes=each * 3 - 1)
    with pytest.raises(SarifError, match="合計"):
        read_sarif(paths, repository, repository, limits)
    assert (
        len(
            read_sarif(
                paths, repository, repository, replace(limits, max_total_bytes=each * 3)
            ).files
        )
        == 3
    )


def test_total_size_default_limit_uses_sparse_files(repository: Path) -> None:
    paths = []
    for n in range(3):
        path = repository.parent / f"{n}.sarif"
        with path.open("wb") as stream:
            stream.truncate(DEFAULT_LIMITS.max_file_bytes)
        paths.append(path)
    with pytest.raises(SarifError, match="合計"):
        read_sarif(paths, repository, repository)


def test_more_than_ten_thousand_findings_are_rejected(repository: Path) -> None:
    results = [result() for _ in range(DEFAULT_LIMITS.max_findings)]
    assert len(read_one(repository, log(run(results))).findings) == 10_000
    with pytest.raises(SarifError, match="10000"):
        read_one(repository, log(run([*results, result()])))


def test_finding_total_counts_across_files_and_runs(repository: Path) -> None:
    first = write(repository.parent, "1.sarif", log(run([result()] * 3), run([result()] * 3)))
    second = write(repository.parent, "2.sarif", log(run([result()] * 3)))
    limits = replace(DEFAULT_LIMITS, max_findings=8)
    with pytest.raises(SarifError, match=r"2\.sarif"):
        read_sarif([first, second], repository, repository, limits)


def test_dropped_results_still_count_toward_the_finding_limit(repository: Path) -> None:
    limits = replace(DEFAULT_LIMITS, max_findings=2)
    with pytest.raises(SarifError):
        read_one(repository, log(run([result(uri=None)] * 3)), limits)


def test_long_strings_are_cut_at_their_limits(repository: Path) -> None:
    finding = only(
        repository,
        result(message="m" * 5000, ruleId="r" * 500),
        name="n" * 500,
        version="v" * 500,
    )
    assert len(finding.tool_name) == 100
    assert len(finding.tool_version) == 100
    assert len(finding.rule_id) == 200
    assert len(finding.message) == 2000


def test_strings_at_the_limit_are_kept_whole(repository: Path) -> None:
    finding = only(repository, result(message="m" * 2000, ruleId="r" * 200), name="n" * 100)
    assert (len(finding.tool_name), len(finding.rule_id), len(finding.message)) == (100, 200, 2000)


def test_control_characters_and_escape_sequences_are_removed(repository: Path) -> None:
    finding = only(
        repository,
        result(
            message="a\x1b[31mred\x1b[0m\x07b" + chr(0x202E) + "c", ruleId="R\x00\x1b]0;title\x07X"
        ),
        name="to\x1b[2Jol",
        version="1\x08.0",
    )
    assert finding.message == "aredbc"
    assert finding.rule_id == "RX"
    assert finding.tool_name == "tool"
    assert finding.tool_version == "1.0"


def test_message_keeps_newline_and_tab(repository: Path) -> None:
    assert only(repository, result(message="a\n\tb")).message == "a\n\tb"


def test_escape_sequence_is_removed_before_cutting(repository: Path) -> None:
    message = "\x1b[31m" * 1000 + "x" * 5
    assert only(repository, result(message=message)).message == "xxxxx"


def test_ids_run_through_files_then_runs_then_results(repository: Path) -> None:
    first = write(
        repository.parent,
        "b.sarif",
        log(
            run([result("a.py"), result("b.py")], name="one"),
            run([result("c.py")], name="two"),
        ),
    )
    second = write(repository.parent, "a.sarif", log(run([result("d.py")], name="three")))
    imported = read_sarif([first, second], repository, repository)
    assert [(f.id, f.tool_name, f.path) for f in imported.findings] == [
        ("S1", "one", "a.py"),
        ("S2", "one", "b.py"),
        ("S3", "two", "c.py"),
        ("S4", "three", "d.py"),
    ]


def test_dropped_results_do_not_take_an_id(repository: Path) -> None:
    imported = read_one(repository, log(run([result("a.py"), result("../x.py"), result("b.py")])))
    assert [f.id for f in imported.findings] == ["S1", "S2"]


def test_file_records_hold_sha256_and_revision_ids(repository: Path) -> None:
    with_revisions = run(
        [result()],
        versionControlProvenance=[
            {"revisionId": "abc123", "repositoryUri": "https://x.invalid"},
            {"branch": "main"},
            {"revisionId": "def456"},
            {"revisionId": "abc123"},
        ],
    )
    first = write(repository.parent, "1.sarif", log(with_revisions))
    second = write(repository.parent, "2.sarif", log(run([result()])))
    imported = read_sarif([first, second], repository, repository)
    assert [(record.path, record.revision_ids) for record in imported.files] == [
        (str(first), ("abc123", "def456")),
        (str(second), ()),
    ]
    assert imported.files[0].sha256 == hashlib.sha256(first.read_bytes()).hexdigest()


def test_directory_is_rejected(repository: Path) -> None:
    with pytest.raises(SarifError, match="通常のファイル"):
        read_sarif([repository], repository, repository)


def test_missing_file_is_rejected_with_its_path(repository: Path) -> None:
    missing = repository.parent / "nope.sarif"
    with pytest.raises(SarifError, match=r"nope\.sarif"):
        read_sarif([missing], repository, repository)


def test_symlink_to_regular_file_is_read(repository: Path) -> None:
    target = write(repository.parent, "target.sarif", log(run([result()])))
    link = repository.parent / "link.sarif"
    link.symlink_to(target)
    assert len(read_sarif([link], repository, repository).findings) == 1


def test_symlink_to_directory_is_rejected(repository: Path) -> None:
    link = repository.parent / "dir-link.sarif"
    link.symlink_to(repository, target_is_directory=True)
    with pytest.raises(SarifError, match="通常のファイル"):
        read_sarif([link], repository, repository)


def test_output_round_trips_through_json(repository: Path) -> None:
    imported = read_one(repository, log(run([result(uri=None), result()])))
    dumped = json.loads(imported.model_dump_json())
    assert dumped["dropped"] == {"no_location": 1}
    assert dumped["findings"][0]["id"] == "S1"
