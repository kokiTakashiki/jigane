import shutil
from pathlib import Path

import pytest

from jigane.flaky.catalog import load_catalog
from jigane.flaky.detector import Detection, detect
from jigane.flaky.gates import gate_kind
from jigane.gates.entry import GateKind
from jigane.gates.line_scope import AllLines

FIXTURES = Path(__file__).parent / "fixtures" / "flaky" / "python"
CATALOG = load_catalog()
BY_ID = {item.id: item for item in CATALOG}
TEST_BODY = "import datetime\n\n\ndef test_a():\n    assert datetime.datetime.now()\n"
AUTOUSE_FREEZE = (
    "import pytest\nfrom freezegun import freeze_time\n\n\n"
    "@pytest.fixture(autouse=True)\ndef _clock():\n"
    "    with freeze_time('2026-01-01'):\n        yield\n"
)


def _materialize(tmp_path: Path, name: str, directory: str = "tests") -> None:
    target = tmp_path / directory
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / name / "conftest.py.txt", target / "conftest.py")
    shutil.copy(FIXTURES / name / "test_a.py.txt", target / "test_a.py")


def _time(tmp_path: Path, paths: list[str]) -> list[Detection]:
    found = detect(tmp_path, paths, CATALOG, AllLines())
    return [d for d in found if d.item_id == "FLK-TIME"]


def test_autouse_freeze_in_conftest_makes_warning(tmp_path: Path) -> None:
    _materialize(tmp_path, "setup-fixed")
    (found,) = _time(tmp_path, ["tests/test_a.py"])
    assert found.undetermined
    assert gate_kind(found, BY_ID) is GateKind.WARNING
    assert "tests/conftest.py:7" in found.reason


def test_conftest_without_autouse_freeze_stays_violation(tmp_path: Path) -> None:
    _materialize(tmp_path, "setup-unfixed")
    (found,) = _time(tmp_path, ["tests/test_a.py"])
    assert not found.undetermined
    assert gate_kind(found, BY_ID) is GateKind.VIOLATION
    assert found.reason == ""


def test_parent_conftest_applies(tmp_path: Path) -> None:
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "conftest.py").write_text(AUTOUSE_FREEZE, encoding="utf-8")
    (tmp_path / "tests" / "unit" / "test_a.py").write_text(TEST_BODY, encoding="utf-8")
    (found,) = _time(tmp_path, ["tests/unit/test_a.py"])
    assert found.undetermined
    assert "tests/conftest.py:7" in found.reason


def test_root_conftest_applies(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "conftest.py").write_text(AUTOUSE_FREEZE, encoding="utf-8")
    (tmp_path / "tests" / "test_a.py").write_text(TEST_BODY, encoding="utf-8")
    (found,) = _time(tmp_path, ["tests/test_a.py"])
    assert "conftest.py:7" in found.reason


def test_sibling_conftest_does_not_apply(tmp_path: Path) -> None:
    (tmp_path / "tests" / "a").mkdir(parents=True)
    (tmp_path / "tests" / "b").mkdir()
    (tmp_path / "tests" / "a" / "conftest.py").write_text(AUTOUSE_FREEZE, encoding="utf-8")
    (tmp_path / "tests" / "b" / "test_a.py").write_text(TEST_BODY, encoding="utf-8")
    (found,) = _time(tmp_path, ["tests/b/test_a.py"])
    assert not found.undetermined


def test_autouse_fixture_in_test_file_itself_applies(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(
        AUTOUSE_FREEZE + "\n\n" + TEST_BODY, encoding="utf-8"
    )
    found = _time(tmp_path, ["tests/test_a.py"])
    assert [d.undetermined for d in found] == [True]
    assert "tests/test_a.py:7" in found[0].reason


def test_time_machine_travel_is_recognized(tmp_path: Path) -> None:
    conftest = (
        "import pytest\nimport time_machine\n\n\n"
        "@pytest.fixture(autouse=True)\ndef _clock():\n"
        "    with time_machine.travel('2026-01-01'):\n        yield\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text(conftest, encoding="utf-8")
    (tmp_path / "tests" / "test_a.py").write_text(TEST_BODY, encoding="utf-8")
    (found,) = _time(tmp_path, ["tests/test_a.py"])
    assert found.undetermined


def test_word_only_in_conftest_is_not_recognized(tmp_path: Path) -> None:
    conftest = (
        "import pytest\nfrom freezegun import freeze_time\n\n\n"
        "@pytest.fixture(autouse=True)\ndef _other():\n    yield\n\n\n"
        "def helper():\n    return freeze_time('2026-01-01')\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text(conftest, encoding="utf-8")
    (tmp_path / "tests" / "test_a.py").write_text(TEST_BODY, encoding="utf-8")
    (found,) = _time(tmp_path, ["tests/test_a.py"])
    assert not found.undetermined


def test_symlinked_conftest_is_not_read(tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "conftest.py"
    outside.parent.mkdir()
    outside.write_text(AUTOUSE_FREEZE, encoding="utf-8")
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "conftest.py").symlink_to(outside)
    (tree / "tests" / "test_a.py").write_text(TEST_BODY, encoding="utf-8")
    (found,) = _time(tree, ["tests/test_a.py"])
    assert not found.undetermined


@pytest.mark.parametrize(
    "broken",
    [
        "@pytest.fixture(autouse=True)\ndef _clock(:\n    pass\n",
        "@pytest.fixture(autouse=True)\ndef _clock()\n    freeze_time('2026-01-01')\n",
        "@pytest.fixture(autouse=True)\nclass _Clock:\n    pass\n",
        "@pytest.fixture(autouse=True)\ndef _clock():\n",
        "@pytest.fixture(autouse=True)\ndef _clock(): freeze_time(\n",
        "@pytest.fixture(autouse=True)\n",
    ],
)
def test_unparsable_autouse_fixture_does_not_fix_the_clock(tmp_path: Path, broken: str) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text(
        "import pytest\n\n\n" + broken, encoding="utf-8"
    )
    (tmp_path / "tests" / "test_a.py").write_text(TEST_BODY, encoding="utf-8")
    (found,) = _time(tmp_path, ["tests/test_a.py"])
    assert not found.undetermined


def test_existing_time_fixtures_are_unchanged(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_bad.py").write_text(
        (FIXTURES / "time-bad.txt").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "tests" / "test_good.py").write_text(
        (FIXTURES / "time-good.txt").read_text(encoding="utf-8"), encoding="utf-8"
    )
    found = _time(tmp_path, ["tests/test_bad.py", "tests/test_good.py"])
    assert {d.path for d in found} == {"tests/test_bad.py"}
    assert not any(d.undetermined for d in found)


RANDOM_BODY = "import random\n\n\ndef test_a():\n    assert random.randint(1, 3)\n"
AUTOUSE_SEED = (
    "import pytest\nimport random\n\n\n"
    "@pytest.fixture(autouse=True)\ndef _seed():\n    random.seed(1)\n"
)


def _found(tmp_path: Path, item_id: str, conftest: str | None, body: str) -> list[Detection]:
    (tmp_path / "tests").mkdir(exist_ok=True)
    if conftest is not None:
        (tmp_path / "tests" / "conftest.py").write_text(conftest, encoding="utf-8")
    (tmp_path / "tests" / "test_a.py").write_text(body, encoding="utf-8")
    found = detect(tmp_path, ["tests/test_a.py"], CATALOG, AllLines())
    return [d for d in found if d.item_id == item_id]


def _patch_conftest(target: str) -> str:
    return (
        "import pytest\n\n\n@pytest.fixture(autouse=True)\n"
        f"def _patch(monkeypatch):\n    monkeypatch.setattr({target}, 1)\n"
    )


def test_autouse_random_seed_in_conftest_makes_warning(tmp_path: Path) -> None:
    (found,) = _found(tmp_path, "FLK-RANDOM", AUTOUSE_SEED, RANDOM_BODY)
    assert found.undetermined
    assert gate_kind(found, BY_ID) is GateKind.WARNING
    assert "tests/conftest.py:7" in found.reason
    assert "種" in found.reason


def test_random_without_any_seed_stays_violation(tmp_path: Path) -> None:
    (found,) = _found(tmp_path, "FLK-RANDOM", None, RANDOM_BODY)
    assert not found.undetermined
    assert found.reason == ""


def test_seed_in_test_function_is_not_detected(tmp_path: Path) -> None:
    body = (
        "import random\n\n\ndef test_a():\n    random.seed(1)\n    assert random.randint(1, 3)\n"
    )
    assert _found(tmp_path, "FLK-RANDOM", None, body) == []


def test_seed_in_non_autouse_fixture_of_conftest_is_not_recognized(tmp_path: Path) -> None:
    conftest = AUTOUSE_SEED.replace("autouse=True", "autouse=False")
    (found,) = _found(tmp_path, "FLK-RANDOM", conftest, RANDOM_BODY)
    assert not found.undetermined


def test_autouse_seed_in_test_file_itself_is_warning(tmp_path: Path) -> None:
    (found,) = _found(tmp_path, "FLK-RANDOM", None, AUTOUSE_SEED + "\n\n" + RANDOM_BODY)
    assert found.undetermined
    assert "tests/test_a.py:7" in found.reason


@pytest.mark.parametrize("target", ["datetime", "'time.time'", "mod.datetime", "mod.time"])
def test_monkeypatch_of_time_target_is_recognized(tmp_path: Path, target: str) -> None:
    (found,) = _found(tmp_path, "FLK-TIME", _patch_conftest(target), TEST_BODY)
    assert found.undetermined
    assert "時刻" in found.reason


@pytest.mark.parametrize("target", ["random", "'random.random'"])
def test_monkeypatch_of_random_target_is_seed_fixation(tmp_path: Path, target: str) -> None:
    (found,) = _found(tmp_path, "FLK-RANDOM", _patch_conftest(target), RANDOM_BODY)
    assert found.undetermined
    assert "種" in found.reason


@pytest.mark.parametrize(
    "target", ["target_object", "get_target()", "'os.environ'", "'time.sleep'"]
)
def test_monkeypatch_of_unknown_target_is_not_recognized(tmp_path: Path, target: str) -> None:
    (found,) = _found(tmp_path, "FLK-TIME", _patch_conftest(target), TEST_BODY)
    assert not found.undetermined


def test_monkeypatch_time_target_does_not_fix_seed(tmp_path: Path) -> None:
    (found,) = _found(tmp_path, "FLK-RANDOM", _patch_conftest("datetime"), RANDOM_BODY)
    assert not found.undetermined


@pytest.mark.parametrize(
    "header",
    [
        "freeze_time('2026-01-01')",
        "freeze_time('2026-01-01') as clock",
        "time_machine.travel('2026-01-01')",
        "other(), travel('2026-01-01')",
    ],
)
def test_literal_with_block_suppresses_time_hit(tmp_path: Path, header: str) -> None:
    body = (
        "import datetime\n\n\ndef test_a():\n"
        f"    with {header}:\n        assert datetime.datetime.now()\n"
    )
    assert _found(tmp_path, "FLK-TIME", None, body) == []


def test_time_hit_in_with_header_is_not_suppressed(tmp_path: Path) -> None:
    body = (
        "import datetime\n\n\ndef test_a():\n"
        "    with freeze_time(datetime.datetime.now()):\n        pass\n"
    )
    assert len(_found(tmp_path, "FLK-TIME", None, body)) == 1


def test_time_hit_after_with_block_is_not_suppressed(tmp_path: Path) -> None:
    body = (
        "import datetime\n\n\ndef test_a():\n"
        "    with freeze_time('2026-01-01'):\n        pass\n"
        "    assert datetime.datetime.now()\n"
    )
    assert len(_found(tmp_path, "FLK-TIME", None, body)) == 1


@pytest.mark.parametrize(
    "layout",
    [
        "@freeze_time('2026-01-01')\ndef test_a():\n    assert datetime.datetime.now()\n",
        "@time_machine.travel('2026-01-01')\ndef test_a():\n    assert datetime.datetime.now()\n",
        (
            "@freeze_time('2026-01-01')\nclass TestA:\n    def test_a(self):\n"
            "        assert datetime.datetime.now()\n"
        ),
    ],
)
def test_decorator_suppresses_time_hit(tmp_path: Path, layout: str) -> None:
    assert _found(tmp_path, "FLK-TIME", None, "import datetime\n\n\n" + layout) == []


def test_unrelated_decorator_does_not_suppress_time_hit(tmp_path: Path) -> None:
    body = "import datetime\n\n\n@mark('x')\ndef test_a():\n    assert datetime.datetime.now()\n"
    assert len(_found(tmp_path, "FLK-TIME", None, body)) == 1


def test_existing_random_fixtures_are_unchanged(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_bad.py").write_text(
        (FIXTURES / "random-bad.txt").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "tests" / "test_good.py").write_text(
        (FIXTURES / "random-good.txt").read_text(encoding="utf-8"), encoding="utf-8"
    )
    found = detect(tmp_path, ["tests/test_bad.py", "tests/test_good.py"], CATALOG, AllLines())
    random_found = [d for d in found if d.item_id == "FLK-RANDOM"]
    assert {d.path for d in random_found} == {"tests/test_bad.py"}
    assert not any(d.undetermined for d in random_found)
