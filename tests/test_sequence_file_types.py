from pathlib import Path

from review_fakes import changed_segment

from jigane.languages.csharp import CSharpAdapter
from jigane.languages.swift import SwiftAdapter
from jigane.policy_materials.classes import _changed_types
from jigane.sequence.index import TypeKey, build_index
from jigane.sequence.indexes import Indexes


def _write(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(source, encoding="utf-8")


def test_private_swift_types_of_the_same_name_stay_separate_per_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "A.swift": "private struct Failure { let reason: String }\n",
            "B.swift": "private struct Failure { let code: Int }\n",
        },
    )
    index = build_index(tmp_path, ["A.swift", "B.swift"], SwiftAdapter())
    assert set(index.types) == {TypeKey("Failure", "A.swift"), TypeKey("Failure", "B.swift")}
    assert list(index.types[TypeKey("Failure", "A.swift")].members) == ["reason"]
    assert list(index.types[TypeKey("Failure", "B.swift")].members) == ["code"]
    assert index.types[TypeKey("Failure", "A.swift")].paths == ("A.swift",)
    reason = index.types[TypeKey("Failure", "A.swift")].members["reason"][0]
    assert reason.owner == TypeKey("Failure", "A.swift")
    assert index.find_type("Failure", "B.swift") is index.types[TypeKey("Failure", "B.swift")]


def test_private_type_extension_in_the_same_file_merges_into_the_file_type(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "A.swift": "fileprivate struct Box { let a: Int }\nextension Box { func f() {} }\n",
            "B.swift": "private struct Box { let b: Int }\n",
        },
    )
    index = build_index(tmp_path, ["A.swift", "B.swift"], SwiftAdapter())
    assert set(index.types) == {TypeKey("Box", "A.swift"), TypeKey("Box", "B.swift")}
    assert sorted(index.types[TypeKey("Box", "A.swift")].members) == ["a", "f"]


def test_public_type_extension_across_files_still_merges(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "A.swift": "struct Box { let a: Int }\n",
            "B.swift": "extension Box { func f() {} }\n",
        },
    )
    index = build_index(tmp_path, ["A.swift", "B.swift"], SwiftAdapter())
    assert list(index.types) == [TypeKey("Box")]
    assert index.types[TypeKey("Box")].paths == ("A.swift", "B.swift")
    assert sorted(index.types[TypeKey("Box")].members) == ["a", "f"]


def test_csharp_file_local_types_of_the_same_name_stay_separate(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "A.cs": "file class Helper { public int A; }\n",
            "B.cs": "file class Helper { public int B; }\n",
        },
    )
    index = build_index(tmp_path, ["A.cs", "B.cs"], CSharpAdapter())
    assert set(index.types) == {TypeKey("Helper", "A.cs"), TypeKey("Helper", "B.cs")}


def test_file_marker_uses_the_path_when_two_directories_share_a_file_name(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "x/T.swift": "private struct Failure { let a: Int }\n",
            "y/T.swift": "private struct Failure { let b: Int }\n",
        },
    )
    index = build_index(tmp_path, ["x/T.swift", "y/T.swift"], SwiftAdapter())
    assert set(index.types) == {TypeKey("Failure", "x/T.swift"), TypeKey("Failure", "y/T.swift")}


def test_changed_types_pick_the_private_type_of_the_changed_file_by_name(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {"A.swift": "private struct Failure {}\n", "B.swift": "private struct Failure {}\n"},
    )
    # 範囲は型の宣言行にも項目にも当たらない。名前とパスの突き合わせだけが当たる
    segment = changed_segment("A.swift", 90, 91, "Failure", None)
    changed = _changed_types(Indexes(tmp_path, ["A.swift", "B.swift"]), [segment])
    assert [t.qualified_name for t in changed] == ["Failure（A.swift）"]
