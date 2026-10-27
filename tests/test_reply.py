import json
from pathlib import Path
from typing import Any

import pytest
from dev_launch import run_tool
from gates_support import write_gates_file

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK, EXIT_PROCESSING_FAILURE
from jigane.reviews.lifecycle import Review, create_feature_review
from jigane.storage.store import data_home


def finding(
    number: int,
    group: str = "micro",
    theme: str | None = None,
    triage: str = "must_read",
    claim: str | None = None,
) -> dict[str, Any]:
    return {
        "id": f"F{number}",
        "severity": "warning",
        "group": group,
        "viewpoint": "D1",
        "claim": claim or f"claim{number}",
        "body": f"body{number}",
        "evidence": [{"path": "src/a.py", "start_line": number, "end_line": number + 1}],
        "adjudication": {
            "kind": "rebuttal",
            "challenge": "c",
            "recheck": [],
            "verdict": "kept",
            "reason": "r",
        },
        "theme": theme,
        "triage": triage,
        "judgment_region": None,
    }


@pytest.fixture
def review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Review:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n" + "x = 1\n" * 50, encoding="utf-8")
    monkeypatch.chdir(root)
    created = create_feature_review(root, ["src"], "p")
    state = {
        "last_number": 6,
        "last_theme_number": 1,
        "stale_groups": [],
        "groups": {
            "macro": [finding(5, "macro")],
            "micro": [
                finding(3, theme="B1"),
                finding(1, theme="B1"),
                finding(2, theme=None, triage="remaining"),
            ],
            "security": [finding(4, "security"), finding(6, "test")],
        },
        "themes": [
            {
                "id": "B1",
                "title": "命名の揺れ",
                "finding_ids": ["F1", "F3"],
                "severity": "warning",
                "triage": "must_read",
            }
        ],
    }
    (created.id.directory() / "findings.json").write_text(json.dumps(state), encoding="utf-8")
    return created


def pick(review: Review, *numbers: str) -> None:
    assert main(["pick", str(review.id), *numbers]) == EXIT_OK


def write_user_template(name: str, body: str) -> None:
    directory = data_home() / "templates"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md.j2").write_text(body, encoding="utf-8")


def report_text(review: Review, tmp_path: Path, template: str = "report") -> str:
    out = tmp_path / "out.md"
    assert (
        main(["export-report", str(review.id), "--template", template, "--out", str(out)])
        == EXIT_OK
    )
    return out.read_text(encoding="utf-8")


def test_report_is_deterministic_and_ordered(review: Review, tmp_path: Path) -> None:
    pick(review, "F1", "F2", "F3", "F4", "F5", "F6")
    text = report_text(review, tmp_path)
    assert text == report_text(review, tmp_path)
    headings = [line for line in text.splitlines() if line.startswith("##")]
    assert headings == [
        "## 概要",
        "## 方針",
        "## 実装",
        "### 命名の揺れ",
        "### その他",
        "## テスト規約",
        "## セキュリティ",
    ]
    assert text.index("**F1**") < text.index("**F3**")
    assert "src/a.py:1-2" in text


def test_objected_finding_is_left_out_of_the_report(review: Review, tmp_path: Path) -> None:
    pick(review, "F1")
    assert main(["object", str(review.id), "F1", "--reason", "意図した挙動"]) == EXIT_OK
    assert "意図した挙動" not in report_text(review, tmp_path)


def test_stale_picked_number_warns(
    review: Review, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pick(review, "F1")
    selection = review.id.directory() / "selection.json"
    data = json.loads(selection.read_text(encoding="utf-8"))
    data["verdicts"]["F99"] = {"kind": "picked"}
    selection.write_text(json.dumps(data), encoding="utf-8")
    text = report_text(review, tmp_path)
    assert "F99" not in text
    assert "F99" in capsys.readouterr().err


def test_zero_findings_still_generates(
    review: Review, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = report_text(review, tmp_path)
    assert "最終的な指摘: 0 件" in text
    assert "0 件" in capsys.readouterr().err


def test_user_template_wins(review: Review, tmp_path: Path) -> None:
    pick(review, "F1")
    write_user_template("report", "custom {{ findings | length }}")
    assert report_text(review, tmp_path) == "custom 1"
    capsys_entries = {
        e.name: e.origin
        for e in __import__(
            "jigane.publishing.rendering", fromlist=["list_templates"]
        ).list_templates()
    }
    assert capsys_entries == {"reply": "bundled", "report": "user"}


@pytest.mark.usefixtures("review")
def test_templates_listing(capsys: pytest.CaptureFixture[str]) -> None:
    write_user_template("mine", "x")
    assert main(["templates", "--json"]) == EXIT_OK
    listed = json.loads(capsys.readouterr().out)
    assert [(e["name"], e["origin"]) for e in listed] == [
        ("mine", "user"),
        ("reply", "bundled"),
        ("report", "bundled"),
    ]
    assert all(Path(e["path"]).is_file() for e in listed)


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("{% if %}", "bad.md.j2）の 1 行目が構文の誤りです"),
        ("{{ nothing_here }}", "1 行目で失敗しました: 'nothing_here' is undefined"),
        ("{{ ''.__class__ }}", "1 行目で失敗しました: access to attribute '__class__'"),
    ],
)
def test_template_errors_exit_2(
    review: Review,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    body: str,
    fragment: str,
) -> None:
    write_user_template("bad", body)
    out = tmp_path / "o.md"
    code = main(["export-report", str(review.id), "--template", "bad", "--out", str(out)])
    assert code == EXIT_PROCESSING_FAILURE
    err = capsys.readouterr().err
    assert fragment in err
    assert not out.exists()


def test_missing_template(review: Review, tmp_path: Path) -> None:
    code = main(
        ["export-report", str(review.id), "--template", "nope", "--out", str(tmp_path / "o")]
    )
    assert code == EXIT_PROCESSING_FAILURE


def test_reply_rejects_feature_review(review: Review, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["export-reply", str(review.id), "--template", "reply"]) == EXIT_PROCESSING_FAILURE
    assert "export-report" in capsys.readouterr().err


def test_report_json_wraps_the_body_like_reply(
    review: Review, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    pick(review, "F1")
    write_user_template("report", "custom {{ findings | length }}")
    capsys.readouterr()  # pick の出力を捨てる
    assert main(["export-report", str(review.id), "--template", "report", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {"body": "custom 1"}
    out = tmp_path / "o.json"
    argv = ["export-report", str(review.id), "--template", "report", "--json", "--out", str(out)]
    assert main(argv) == EXIT_OK
    assert json.loads(out.read_text(encoding="utf-8")) == {"body": "custom 1"}
    assert capsys.readouterr().out == ""


def test_report_without_out_writes_the_body_to_stdout(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    pick(review, "F1")
    write_user_template("report", "custom {{ findings | length }}")
    capsys.readouterr()  # pick の出力を捨てる
    assert main(["export-report", str(review.id), "--template", "report"]) == EXIT_OK
    assert capsys.readouterr().out == "custom 1"


def test_report_json_failure_prints_the_error_object(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["export-report", str(review.id), "--template", "nope", "--json"])
    assert code == EXIT_PROCESSING_FAILURE
    captured = capsys.readouterr()
    error = json.loads(captured.out)["error"]
    assert set(error) == {"kind", "title", "reason", "action"}
    assert error["reason"] in captured.err


def assert_no_repeated_blank_lines(text: str) -> None:
    # rumdl の MD012 に当たる。連続する空行と、末尾に重なる空行を許さない
    assert "\n\n\n" not in text
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_report_has_no_repeated_blank_lines(review: Review, tmp_path: Path) -> None:
    pick(review, "F1", "F2", "F3", "F4", "F5", "F6")
    assert_no_repeated_blank_lines(report_text(review, tmp_path))


def gate_entry(number: str, line: int) -> dict[str, Any]:
    return {
        "number": number,
        "id": "S1",
        "explanation": {"text": "懸念の文。理由の文。向きの文。"},
        "cause": "c",
        "confidence": "direct",
        "path": "tests/test_a.py",
        "line": line,
        "excerpt": "x",
        "source": "flaky",
        "reason": "",
        "origin": "o",
        "legitimate": "",
    }


def test_each_gate_violation_is_its_own_bullet(review: Review, tmp_path: Path) -> None:
    gates = {
        "violations": [gate_entry("G1", 3), gate_entry("G2", 9)],
        "warnings": [gate_entry("W1", 5), gate_entry("W2", 7)],
    }
    write_gates_file(review.id.directory(), gates)
    pick(review, "F1")
    text = report_text(review, tmp_path)
    lines = text.splitlines()
    assert "- **G1** `tests/test_a.py:3` 懸念の文。理由の文。向きの文。" in lines
    assert "- **G2** `tests/test_a.py:9` 懸念の文。理由の文。向きの文。" in lines
    assert_no_repeated_blank_lines(text)


def test_an_allowed_regex_tag_is_shown_with_its_reason_and_not_counted(
    review: Review, tmp_path: Path
) -> None:
    allowed = gate_entry("G1", 3) | {
        "source": "regex",
        "severity": "nit",
        "id": "R6",
        "allowed": "外部の仕様のため",
    }
    gates = {"format": 2, "violations": [allowed], "warnings": []}
    write_gates_file(review.id.directory(), gates)
    pick(review, "F1")
    text = report_text(review, tmp_path, "reply")
    assert "- **G1**（nit）外しました（外部の仕様のため） `tests/test_a.py:3`" in text
    assert "ゲート違反 1 件" not in text


HOSTILE_BODY = (
    "outputs['rebuttal'][i] と __all__ と *x* と <tag> と `code` と # と | と & と ~~z~~\n"
    "- 入れ子に見える行\n"
    "1. 番号に見える行\n"
    "---\n"
    "# 見出しに見える行"
)


def _hostile_review(review: Review) -> None:
    path = review.id.directory() / "findings.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    for item in state["groups"]["micro"] + state["groups"]["macro"]:
        item["claim"] = "`code` と outputs['rebuttal'][i] と __all__"
        item["body"] = HOSTILE_BODY
    state["themes"][0]["title"] = "*x* と <tag> と #"
    path.write_text(json.dumps(state), encoding="utf-8")


@pytest.mark.parametrize("template", ["report", "reply"])
def test_generated_text_passes_rumdl(
    review: Review,
    tmp_path: Path,
    template: str,
) -> None:
    _hostile_review(review)
    pick(review, "F1", "F2", "F3", "F5")
    assert main(["object", str(review.id), "F1", "--reason", "[i] と __all__"]) == EXIT_OK
    out = tmp_path / f"{template}.md"
    assert (
        main(["export-report", str(review.id), "--template", template, "--out", str(out)])
        == EXIT_OK
    )
    # 設定を読まず既定の規則で検査する。違反があれば run_tool が例外を投げる
    run_tool(["rumdl", "check", "--isolated", "--disable", "MD013,MD041", str(out)])


def test_abbreviated_option_is_rejected_by_argparse(review: Review) -> None:
    with pytest.raises(SystemExit):
        main(["export-reply", str(review.id), "--template", "reply", "--ou", "x"])


def add_macro_nit(review: Review, number: int, viewpoint: str) -> None:
    path = review.id.directory() / "findings.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    nit = finding(number, "macro", claim=f"nit-claim{number}")
    nit["severity"] = "nit"
    nit["viewpoint"] = viewpoint
    state["groups"]["macro"].append(nit)
    path.write_text(json.dumps(state), encoding="utf-8")


def test_picked_discarded_nit_goes_into_the_text(review: Review, tmp_path: Path) -> None:
    # 捨てた候補の nit でも、利用者が下書きに入れたものは返信の文面に載る
    add_macro_nit(review, 7, "discarded")
    add_macro_nit(review, 8, "M2")
    add_macro_nit(review, 9, "discarded")
    pick(review, "F5", "F7", "F8")
    out = tmp_path / "reply.md"
    assert (
        main(["export-report", str(review.id), "--template", "report", "--out", str(out)])
        == EXIT_OK
    )
    text = out.read_text(encoding="utf-8")
    assert "claim5" in text
    assert "nit-claim7" in text
    assert "nit-claim8" in text
    # 選んでいない捨てた候補は載らない
    assert "nit-claim9" not in text


def test_show_json_marks_whether_each_finding_goes_into_the_reply(
    review: Review, capsys: pytest.CaptureFixture[str]
) -> None:
    add_macro_nit(review, 7, "discarded")
    add_macro_nit(review, 8, "M2")
    add_macro_nit(review, 9, "discarded")
    pick(review, "F9")
    capsys.readouterr()
    assert main(["show", str(review.id), "macro", "--json"]) == EXIT_OK
    findings = json.loads(capsys.readouterr().out)["findings"]
    in_reply = {f["id"]: f["in_reply"] for f in findings}
    # 捨てた候補から写した nit も、ほかの nit と同じに扱う。選んでいなくても除かない
    assert in_reply["F7"] is True
    assert in_reply["F8"] is True
    assert in_reply["F5"] is True
    assert in_reply["F9"] is True
