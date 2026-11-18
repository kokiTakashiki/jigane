from pathlib import Path

from pr_support import commit_all, git

from jigane.git.revision import CommitSha
from jigane.judgment_map.configuration import Config
from jigane.policy_materials.generated_marks import (
    MAX_CHECKED_PATHS,
    GeneratedOrigin,
    MarksRead,
    MarkState,
    MarksUnreadable,
    _parse_triples,
    generated_origins,
    read_marks,
)


def make_repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, CommitSha]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    for name, text in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return repo, CommitSha(commit_all(repo, "head").strip())


MARKS = (
    "/Makefile linguist-generated\n"
    "/justfile linguist-generated=false\n"
    "sub/** -linguist-generated\n"
    "sub/gen.txt linguist-generated\n"
)


def test_only_set_is_marked(tmp_path: Path) -> None:
    files = {".gitattributes": MARKS, "Makefile": "", "justfile": "", "sub/a.py": "", "x.py": ""}
    repo, head = make_repo(tmp_path, files)
    reading = read_marks(repo, head, ["Makefile", "justfile", "sub/a.py", "x.py"])
    assert isinstance(reading, MarksRead)
    assert dict(reading.states) == {
        "Makefile": MarkState.MARKED,
        "justfile": MarkState.UNMARKED,
        "sub/a.py": MarkState.UNMARKED,
        "x.py": MarkState.UNMARKED,
    }
    assert reading.has_marked
    assert reading.unchecked_count == 0


def test_reads_head_tree_not_working_tree(tmp_path: Path) -> None:
    repo, head = make_repo(tmp_path, {".gitattributes": MARKS, "Makefile": ""})
    (repo / ".gitattributes").unlink()
    reading = read_marks(repo, head, ["Makefile"])
    assert isinstance(reading, MarksRead)
    assert reading.state("Makefile") is MarkState.MARKED


def test_no_gitattributes_leaves_everything_unmarked(tmp_path: Path) -> None:
    repo, head = make_repo(tmp_path, {"Makefile": ""})
    reading = read_marks(repo, head, ["Makefile"])
    assert isinstance(reading, MarksRead)
    assert not reading.has_marked
    assert reading.state("Makefile") is MarkState.UNMARKED


def test_git_failure_is_unreadable_not_unmarked(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path, {".gitattributes": MARKS, "Makefile": ""})
    reading = read_marks(repo, CommitSha("0" * 40), ["Makefile"])
    assert isinstance(reading, MarksUnreadable)


def test_paths_over_limit_are_unchecked_and_counted(tmp_path: Path) -> None:
    repo, head = make_repo(tmp_path, {".gitattributes": "* linguist-generated\n", "a": ""})
    paths = [f"f{index}" for index in range(MAX_CHECKED_PATHS + 3)]
    reading = read_marks(repo, head, paths)
    assert isinstance(reading, MarksRead)
    assert reading.unchecked_count == 3
    assert reading.state(paths[0]) is MarkState.MARKED
    assert [reading.state(path) for path in paths[-3:]] == [MarkState.UNCHECKED] * 3


def test_macro_attribute_marks(tmp_path: Path) -> None:
    attributes = "[attr]generated linguist-generated\n*.gen generated\n"
    repo, head = make_repo(tmp_path, {".gitattributes": attributes, "a.gen": "", "b.py": ""})
    reading = read_marks(repo, head, ["a.gen", "b.py"])
    assert isinstance(reading, MarksRead)
    assert reading.state("a.gen") is MarkState.MARKED
    assert reading.state("b.py") is MarkState.UNMARKED


def test_dash_leading_path_is_not_an_option(tmp_path: Path) -> None:
    repo, head = make_repo(tmp_path, {".gitattributes": "* linguist-generated\n", "-n": ""})
    reading = read_marks(repo, head, ["-n"])
    assert isinstance(reading, MarksRead)
    assert reading.state("-n") is MarkState.MARKED


def test_origins_put_exclude_before_mark(tmp_path: Path) -> None:
    attributes = "* linguist-generated\n/hand.py -linguist-generated\n"
    repo, head = make_repo(tmp_path, {".gitattributes": attributes, "uv.lock": "", "a": ""})
    paths = ["uv.lock", "Makefile", "hand.py"]
    reading = read_marks(repo, head, paths)
    config = Config(exclude=("*.lock",))
    assert generated_origins(paths, reading, config) == {
        "uv.lock": GeneratedOrigin.EXCLUDE,
        "Makefile": GeneratedOrigin.LINGUIST_GENERATED,
        "hand.py": GeneratedOrigin.NONE,
    }


def test_origins_without_reading_are_none_or_exclude(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path, {"a": ""})
    reading = read_marks(repo, CommitSha("0" * 40), ["Makefile", "uv.lock"])
    assert generated_origins(["Makefile", "uv.lock"], reading, Config(exclude=("*.lock",))) == {
        "Makefile": GeneratedOrigin.NONE,
        "uv.lock": GeneratedOrigin.EXCLUDE,
    }


def test_duplicate_paths_are_checked_once(tmp_path: Path) -> None:
    repo, head = make_repo(tmp_path, {".gitattributes": "* linguist-generated\n", "a": ""})
    reading = read_marks(repo, head, ["a", "a"])
    assert isinstance(reading, MarksRead)
    assert dict(reading.states) == {"a": MarkState.MARKED}


def test_broken_output_is_not_parsed() -> None:
    assert _parse_triples(b"a\0linguist-generated\0set\0", ["a"]) == {"a": MarkState.MARKED}
    assert _parse_triples(b"a\0linguist-generated\0set", ["a"]) is None
    assert _parse_triples(b"a\0linguist-generated\0", ["a"]) is None
    assert _parse_triples(b"b\0linguist-generated\0set\0", ["a"]) is None
    assert _parse_triples(b"a\0other\0set\0", ["a"]) is None
    assert _parse_triples(b"\xff\0linguist-generated\0set\0", ["a"]) is None
