"""古い形の保存物の素材（tests/fixtures/store）を、今の読み口が版に応じて扱うことを縛る。

findings.json、findings_history.json、background.json、bot_comments.json、gates.json、map.json、
review.json、selection.json、runs.json、run_in_progress.json が古い版の素材は、
作り直しを案内して止まる。map.json と review.json は全部の素材が古い版で、
レビューを開く読み口は全素材で止まる。
それ以外の保存物は、今の読み口が読める。
"""

import json
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_PROCESSING_FAILURE
from jigane.findings.records import (
    FINDINGS_FILE,
    FINDINGS_FORMAT,
    HISTORY_FILE,
    IN_PROGRESS_FILE,
    RUNS_FILE,
    load_findings_state,
    load_history_checked,
)
from jigane.flaky.gates import GATES_FILE, load_gates
from jigane.gates.entry import GATES_FORMAT
from jigane.judgment_map.map_kind import MAP_FILE, MAP_FORMAT
from jigane.pull_request_context.background import BACKGROUND_FILE, BACKGROUND_FORMAT
from jigane.pull_request_context.bots import (
    BOT_COMMENTS_FILE,
    BOT_COMMENTS_FORMAT,
    read_bot_comments,
)
from jigane.reviews.catalog import read_review_at
from jigane.reviews.review_formats import REVIEW_FORMAT
from jigane.reviews.selection import SELECTION_FILE, SELECTION_FORMAT, load_selection
from jigane.skills import pipeline, run_record
from jigane.skills.mcp import McpSelection, SecurityConfigError
from jigane.skills.pipeline import RUNS_FORMAT
from jigane.skills.run_record import IN_PROGRESS_FORMAT, STOPPED_FILE, STOPPED_FORMAT
from jigane.storage.store import REVIEW_FILE
from jigane.storage.store_format import OlderFormatError

if TYPE_CHECKING:
    from collections.abc import Callable

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


CURRENT_FORMATS = {
    FINDINGS_FILE: FINDINGS_FORMAT,
    HISTORY_FILE: FINDINGS_FORMAT,
    BACKGROUND_FILE: BACKGROUND_FORMAT,
    BOT_COMMENTS_FILE: BOT_COMMENTS_FORMAT,
    GATES_FILE: GATES_FORMAT,
    MAP_FILE: MAP_FORMAT,
    REVIEW_FILE: REVIEW_FORMAT,
    SELECTION_FILE: SELECTION_FORMAT,
    RUNS_FILE: RUNS_FORMAT,
    IN_PROGRESS_FILE: IN_PROGRESS_FORMAT,
    STOPPED_FILE: STOPPED_FORMAT,
}


def is_older(stored: Path, file: str) -> bool:
    """素材がその保存物を持ち、版が今の版より古い。format を持たない素材は版 1。"""
    path = stored / file
    if not path.is_file():
        return False
    version: int = json.loads(path.read_text(encoding="utf-8")).get("format", 1)
    return version < CURRENT_FORMATS[file]


def opening_stops(stored: Path) -> bool:
    """レビューを開くと読む review.json、map.json、background.json のどれかが古い版か。"""
    return any(is_older(stored, file) for file in (REVIEW_FILE, MAP_FILE, BACKGROUND_FILE))


def test_review_stops_and_points_to_a_rebuild(stored: Path) -> None:
    # review.json と map.json の素材は全部が古い版（または版の鍵が無い）。開く読み口は止まる
    assert opening_stops(stored)
    with pytest.raises(OlderFormatError, match="jigane new") as caught:
        read_review_at(stored)
    assert "引き継がれません" in str(caught.value)


def _read_in_progress(stored: Path) -> object:
    return run_record.load_in_progress(
        json.loads((stored / IN_PROGRESS_FILE).read_text(encoding="utf-8"))
    )


def test_stored_readers(stored: Path) -> None:
    if is_older(stored, FINDINGS_FILE):
        # findings.json の素材は全部が古い版。読み口は作り直しを案内して止まる
        with pytest.raises(OlderFormatError, match="jigane new"):
            load_findings_state(stored)
    else:
        load_findings_state(stored)
    if is_older(stored, HISTORY_FILE):
        [warning] = load_history_checked(stored).warnings
        assert "jigane new" in warning.description()
    else:
        # 警告を返して空にする読み口は、読めなかった扱いにする
        assert load_history_checked(stored).warnings == ()
    readers: dict[str, Callable[[Path], object]] = {
        BOT_COMMENTS_FILE: read_bot_comments,
        GATES_FILE: load_gates,
        RUNS_FILE: pipeline.read_runs,
        SELECTION_FILE: load_selection,
    }
    if (stored / IN_PROGRESS_FILE).is_file():
        readers[IN_PROGRESS_FILE] = _read_in_progress
    for file, read in readers.items():
        if is_older(stored, file):
            with pytest.raises(OlderFormatError, match="jigane new"):
                read(stored)
        else:
            read(stored)


def test_show_json_stops_and_points_to_a_rebuild(
    stored: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review_id = json.loads((stored / "review.json").read_text(encoding="utf-8"))["id"]
    assert opening_stops(stored)
    assert main(["show", review_id, "--json"]) == EXIT_PROCESSING_FAILURE
    assert "jigane new" in capsys.readouterr().err


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
    before = (home / "mcp.json").read_bytes()
    with pytest.raises(SecurityConfigError, match=r"旧 mcp\.json"):
        McpSelection.load(("mobile-sec", "server-sec"))
    assert (home / "mcp.json").read_bytes() == before
