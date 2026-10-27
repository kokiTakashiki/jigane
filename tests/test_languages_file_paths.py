import pytest

from jigane.languages.base import (
    FilePathAdapter,
    FilePathPattern,
    FileUsage,
    LanguageAdapter,
    parse,
)
from jigane.languages.csharp import CSharpAdapter
from jigane.languages.java import JavaAdapter
from jigane.languages.python import PythonAdapter
from jigane.languages.swift import SwiftAdapter


def patterns(code: str) -> list[FilePathPattern]:
    python = PythonAdapter()
    source = code.encode()
    return python.file_path_patterns(parse(python, source).root_node, source, "r.py")


def pairs(code: str) -> list[tuple[str, FileUsage]]:
    return [(p.expression, p.usage) for p in patterns(code)]


def test_open_write_and_read_modes() -> None:
    code = (
        'open(f"{prefix}.csv", "w")\nopen("a.bin", "wb")\nopen("b.txt")\nopen("c.txt", mode="a")\n'
    )
    assert pairs(code) == [
        ('f"{prefix}.csv"', FileUsage.WRITE),
        ('"a.bin"', FileUsage.WRITE),
        ('"b.txt"', FileUsage.READ),
        ('"c.txt"', FileUsage.WRITE),
    ]


def test_path_methods_use_receiver() -> None:
    code = (
        "def f(out, prefix, i):\n"
        '    (out / f"{prefix}_{i:03d}.png").write_bytes(b"")\n'
        '    Path("x.json").read_text()\n'
        '    Path("y.log").open("w")\n'
    )
    result = patterns(code)
    assert [(p.function, p.usage) for p in result] == [
        ("f", FileUsage.WRITE),
        ("f", FileUsage.READ),
        ("f", FileUsage.WRITE),
    ]
    assert result[0].expression == '(out / f"{prefix}_{i:03d}.png")'


def test_variable_receiver_is_resolved_one_step_from_previous_assignment() -> None:
    code = (
        "def f(out, prefix):\n"
        '    target = out / "old.txt"\n'
        '    target = out / f"{prefix}_sheet.png"\n'
        "    target.write_text('x')\n"
        "    other.write_text('y')\n"
    )
    result = patterns(code)
    assert [(p.expression, p.usage) for p in result] == [
        ('out / f"{prefix}_sheet.png"', FileUsage.WRITE),
        ("other", FileUsage.WRITE),
    ]
    assert result[0].line == 4


def test_placeholders_stay_verbatim() -> None:
    assert pairs('Path(f"{prefix}_{index:03d}.png")\n') == [
        ('Path(f"{prefix}_{index:03d}.png")', FileUsage.UNKNOWN)
    ]


def test_plain_path_without_string_building_is_ignored() -> None:
    assert patterns('Path("a") / "b"\nPath(name)\n') == []


def test_string_concatenation_in_path() -> None:
    assert pairs('Path(base + "/x.txt")\n') == [('Path(base + "/x.txt")', FileUsage.UNKNOWN)]


def test_outermost_path_expression_only_once() -> None:
    code = 'p = Path(root) / f"{a}" / "b.txt"\n'
    assert pairs(code) == [('Path(root) / f"{a}" / "b.txt"', FileUsage.UNKNOWN)]


@pytest.mark.parametrize("adapter", [CSharpAdapter(), JavaAdapter(), SwiftAdapter()])
def test_other_languages_do_not_offer_the_reader(adapter: LanguageAdapter) -> None:
    # 未対応の言語は口を持たない。該当なし（空の一覧）と区別するため
    assert not isinstance(adapter, FilePathAdapter)


def test_wrapper_read_text_keeps_whole_call() -> None:
    code = (
        "def f(environment):\n"
        '    environment.read_text(environment.repository_path(".python-version"))\n'
    )
    assert pairs(code) == [
        (
            'environment.read_text(environment.repository_path(".python-version"))',
            FileUsage.READ,
        )
    ]


def test_path_vocabulary_unlink_mkdir_is_file_scandir() -> None:
    code = (
        "def f(d, name):\n"
        "    Path(d).unlink()\n"
        '    (d / f"{name}.x").mkdir(parents=True)\n'
        '    (d / f"{name}.y").is_file()\n'
        "    os.scandir(d)\n"
    )
    assert pairs(code) == [
        ("Path(d)", FileUsage.WRITE),
        ('(d / f"{name}.x")', FileUsage.WRITE),
        ('(d / f"{name}.y")', FileUsage.READ),
        ("d", FileUsage.READ),
    ]


def test_project_wrapper_is_unknown_and_reported_once() -> None:
    code = (
        "def f(environment, relative):\n"
        "    environment.path_exists(environment.repository_path(relative))\n"
        "    environment.run_command(['uv'])\n"
    )
    assert pairs(code) == [
        ("environment.path_exists(environment.repository_path(relative))", FileUsage.UNKNOWN)
    ]


def test_dir_entry_methods_are_not_path_methods() -> None:
    code = (
        "def f(directory):\n"
        "    with os.scandir(directory) as entries:\n"
        "        ordered = sorted(entries)\n"
        "    for entry in ordered:\n"
        "        entry.is_file()\n"
        "        entry.is_dir(follow_symlinks=False)\n"
    )
    assert pairs(code) == [("directory", FileUsage.READ)]


def test_exists_and_is_dir_need_a_receiver_known_to_be_a_path() -> None:
    code = (
        "def f(candidate, root: Path, optional: Path | None = None, other: str = ''):\n"
        "    candidate.exists()\n"
        "    other.is_dir()\n"
        "    root.exists()\n"
        "    optional.is_dir()\n"
        '    (root / "a").exists()\n'
        '    Path("b").is_dir()\n'
        '    base = root.parent / "c"\n'
        "    base.exists()\n"
        "    root.parent.mkdir()\n"
        "    root.resolve().is_file()\n"
    )
    assert pairs(code) == [
        ("root", FileUsage.READ),
        ("optional", FileUsage.READ),
        ('(root / "a")', FileUsage.READ),
        ('Path("b")', FileUsage.READ),
        ('root.parent / "c"', FileUsage.READ),
        ("root.parent", FileUsage.WRITE),
        ("root.resolve()", FileUsage.READ),
    ]


def test_wrapper_without_a_file_word_in_its_name_is_not_reported() -> None:
    code = (
        "def f(environment, name):\n"
        '    environment.modified_at(environment.home_path(f"x/{name}"))\n'
        '    environment.path_exists(environment.home_path(f"x/{name}"))\n'
    )
    assert pairs(code) == [
        ('environment.path_exists(environment.home_path(f"x/{name}"))', FileUsage.UNKNOWN)
    ]


def test_call_of_a_call_result_is_not_a_file_access() -> None:
    assert pairs("def f(handlers, p):\n    handlers[0](p)\n    make()(p)\n") == []


def test_module_function_without_arguments_is_not_reported() -> None:
    assert pairs("os.mkdir()\nos.path.exists()\n") == []


def test_receiver_that_is_not_a_known_path_is_not_reported() -> None:
    assert pairs('"abc".exists()\n[1].exists()\n') == []


def test_path_chain_is_followed_only_to_the_resolve_depth() -> None:
    chain = 'Path("x")' + ".parent" * 5
    assert pairs(f"{chain}.exists()\n") == [(chain, FileUsage.READ)]
    assert pairs(f"{chain + '.parent'}.exists()\n") == []
