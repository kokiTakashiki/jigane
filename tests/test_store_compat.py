"""古い形の保存物の素材（tests/fixtures/store）を、今の読み口が読めることを縛る。"""

import re
import shutil
from pathlib import Path

import pytest

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.findings.records import load_findings_state, load_history_checked
from jigane.flaky.gates import load_gates
from jigane.groups.definitions import GroupId
from jigane.reviews.lifecycle import read_review_at
from jigane.reviews.selection import load_selection
from jigane.skills import pipeline
from jigane.skills.mcp import McpSelection

STORE = Path(__file__).parent / "fixtures" / "store"
# 置き場の根の素材と、読めないと分けた素材は、レビューの置き場ではない
NOT_REVIEWS = {"root", "unreadable"}


def review_directories() -> list[Path]:
    return sorted(
        review.parent
        for review in STORE.glob("*/*/*/*/*/review.json")
        if review.relative_to(STORE).parts[0] not in NOT_REVIEWS
    )


def fixture_name(directory: Path) -> str:
    return "-".join(directory.relative_to(STORE).parts[:2])


@pytest.fixture(params=review_directories(), ids=fixture_name)
def stored(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    """素材を一時の JIGANE_HOME の下の、識別子が指す位置へ写す。"""
    source: Path = request.param
    owner, repo, name = source.relative_to(STORE).parts[2:]
    target = tmp_path / "home" / owner / repo / name
    shutil.copytree(source, target)
    return target


def test_catalog_matches_fixtures() -> None:
    catalog = (STORE / "CATALOG.md").read_text(encoding="utf-8")
    listed = re.findall(r"^\| ([a-z_]+/s\d{2}/\S+) \|", catalog, flags=re.MULTILINE)
    present = [path.relative_to(STORE).as_posix() for path in review_directories()]
    assert sorted(listed) == sorted(present)


def test_review_opens(stored: Path) -> None:
    located = read_review_at(stored)
    assert located.directory == stored


def test_stored_readers(stored: Path) -> None:
    read_review_at(stored)
    load_findings_state(stored)
    # 警告を返して空にする読み口は、読めなかった扱いにする
    assert load_history_checked(stored).warnings == ()
    load_gates(stored)
    pipeline.read_runs(stored)
    load_selection(stored)


def test_show_json(stored: Path, capsys: pytest.CaptureFixture[str]) -> None:
    review_id = str(read_review_at(stored).review.id)
    assert main(["show", review_id, "--json"]) == EXIT_OK
    for group in GroupId:
        assert main(["show", review_id, str(group), "--json"]) == EXIT_OK
    capsys.readouterr()


# 素材に出てはならない語。製品名の jigane は、鍵 jigane_version を除いた残りで調べる
PRIVATE_WORDS = (
    "koki-mobile",
    "kokitakashiki",
    "MinecraftConfigurations",
    "/Users/",
    "~/.local",
    "gmail",
)


def test_no_private_names() -> None:
    for path in sorted(STORE.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for word in PRIVATE_WORDS:
            assert word not in text, f"{path.relative_to(STORE)} に {word} がある"
        remaining = text.replace("jigane_version", "")
        assert "jigane" not in remaining, f"{path.relative_to(STORE)} に jigane がある"


def test_root_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "root-home"
    monkeypatch.setenv("JIGANE_HOME", str(home))
    home.mkdir()
    shutil.copy(STORE / "root" / "mcp" / "mcp.json", home)
    selection = McpSelection.load(("mobile-sec", "server-sec"))
    assert selection.names == ("mobile-sec", "server-sec")
