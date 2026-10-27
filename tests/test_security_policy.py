from pathlib import Path

from markdown_support import headings, section_prose

SECURITY = Path(__file__).resolve().parent.parent / "SECURITY.md"

HEADINGS = (
    "## 脆弱性の報告",
    "## 対象",
    "## 守っている前提",
    "### データと外部コマンド",
    "### レビューの入力と Claude Code",
    "### 外の言語",
    "### 費用の上限",
)
# 脅威の前提として利用者に読ませる語。コードの旗や変数の名前が変わったら、文書も直す
REQUIRED_TERMS = ("--restricted", "--strict-mcp-config", "JIGANE_CLAUDE_BINARY")


def test_security_policy_has_headings() -> None:
    found = headings(SECURITY.read_text(encoding="utf-8"))
    assert [heading for heading in HEADINGS if heading not in found] == []


def test_security_policy_names_threat_model_terms() -> None:
    # 脅威の前提は「守っている前提」の節で述べる。他の節に書かれていても満たしたことにしない
    section = section_prose(SECURITY.read_text(encoding="utf-8"), "## 守っている前提")
    assert [term for term in REQUIRED_TERMS if term not in section] == []


ROOT = Path(__file__).resolve().parent.parent
CONTRIBUTING = ROOT / "CONTRIBUTING.md"
PR_TEMPLATE = ROOT / ".github" / "pull_request_template.md"


def test_contributing_refuses_pull_requests() -> None:
    text = CONTRIBUTING.read_text(encoding="utf-8")
    assert "Pull Request は受け付けません" in text


def test_contributing_points_to_security_policy() -> None:
    text = CONTRIBUTING.read_text(encoding="utf-8")
    assert "SECURITY.md" in text


def test_pull_request_template_points_to_contributing() -> None:
    text = PR_TEMPLATE.read_text(encoding="utf-8")
    assert "CONTRIBUTING.md" in text


README = ROOT / "README.md"
NOTICE_HEADING = "## データの送り先と利用の条件"


def _notice_section() -> str:
    return section_prose(README.read_text(encoding="utf-8"), NOTICE_HEADING)


def test_readme_notice_names_destinations_and_terms() -> None:
    section = _notice_section()
    required = (
        "https://code.claude.com/docs/en/data-usage",
        "https://code.claude.com/docs/en/legal-and-compliance",
        "非公式",
    )
    assert [term for term in required if term not in section] == []


def test_readme_has_external_language_section() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## 外の言語を足す" in headings(text)
