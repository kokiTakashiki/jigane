import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_noqa_reasons.py"


def load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_noqa_reasons", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def offending_lines(source: str) -> list[int]:
    lines: list[int] = load_checker().lines_without_reason(source)
    return lines


def test_noqa_without_reason_is_reported_with_its_line() -> None:
    assert offending_lines("x = 1\ny = 2  # noqa: E501\n") == [2]


def test_noqa_with_multiple_codes_and_no_reason_is_reported() -> None:
    assert offending_lines("x = 1  # noqa: E501, ANN401\n") == [1]


def test_blanket_noqa_is_reported() -> None:
    assert offending_lines("x = 1  # noqa\n") == [1]


def test_separator_alone_is_not_a_reason() -> None:
    assert offending_lines("x = 1  # noqa: E501 -\n") == [1]


def test_noqa_with_reason_passes() -> None:
    source = "a = 1  # noqa: E501 長い行を折ると意味が変わる\nb = 2  # noqa: ARG001 - 同上\n"
    assert offending_lines(source) == []


def test_noqa_inside_string_or_file_level_noqa_is_ignored() -> None:
    assert offending_lines('s = "x  # noqa: E501"\n# ruff: noqa: E501\n') == []


def test_repository_has_no_noqa_without_reason() -> None:
    assert load_checker().find_offenders(ROOT) == []


def test_noqa_after_other_comment_text_is_reported() -> None:
    assert offending_lines("x = 1  # 説明 # noqa: E501\n") == [1]


def test_space_separated_codes_without_reason_are_reported() -> None:
    assert offending_lines("x = 1  # noqa: E501 F401\n") == [1]


def test_space_separated_codes_followed_by_reason_pass() -> None:
    assert offending_lines("x = 1  # noqa: E501 F401 どちらも折れない\n") == []


def test_unreadable_source_fails_the_check(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("x = (\n", encoding="utf-8")
    checker = load_checker()
    assert checker.find_offenders(tmp_path) == ["broken.py（構文を読めない）"]
    assert checker.main([str(tmp_path)]) == 1
