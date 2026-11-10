from jigane.design.submodule_status import (
    ConflictedSubmodule,
    DifferentCommitSubmodule,
    UninitializedSubmodule,
    UnrecordedSubmodule,
    classify_status,
)


def test_classify_status_covers_every_mark_without_running_git() -> None:
    status = (
        f"-{'a' * 40} cli\n+{'b' * 40} lib (heads/x)\nU{'0' * 40} mrg\n {'c' * 40} same\n"
        "not a status line\n"
    )
    notes = classify_status(status, lambda path: "d" * 40 if path == "lib" else None)
    assert notes == (
        UninitializedSubmodule("cli", "a" * 40),
        DifferentCommitSubmodule("lib", "d" * 40, "b" * 40),
        ConflictedSubmodule("mrg"),
    )
    assert classify_status(f"+{'b' * 40} lib\n", lambda _: None) == (
        UnrecordedSubmodule("lib", "b" * 40),
    )
