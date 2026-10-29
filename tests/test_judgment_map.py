import json
from pathlib import Path

import pytest

from jigane.judgment_map.classification import (
    BotTouch,
    ChangeKind,
    FilePair,
    FindingTouch,
    JudgmentMap,
    Segment,
    apply_bot_touches,
    apply_static_findings,
    build_judgment_map,
    build_regions,
    classify,
    file_line_counts,
)
from jigane.judgment_map.configuration import DEFAULT_EXCLUDE, Config, load_config
from jigane.judgment_map.kinds import LEVEL_OF_KIND, Kind, Level, StoredMode
from jigane.judgment_map.map_kind import _upgrade_v1
from jigane.languages.base import Span
from jigane.preconditions.required import required


def write(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def run(
    tmp_path: Path,
    old: dict[str, str] | None,
    new: dict[str, str],
    pairs: list[FilePair],
    config: Config | None = None,
) -> list[Segment]:
    old_root = write(tmp_path / "old", old) if old is not None else None
    new_root = write(tmp_path / "new", new)
    return classify(old_root, new_root, pairs, config or Config())


def same(name: str) -> list[FilePair]:
    return [FilePair(name, name)]


def kinds(segments: list[Segment]) -> list[Kind]:
    return [s.kind for s in segments]


def test_comment_only(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def f():\n    return 1\n"},
        {"a.py": "def f():\n    # note\n    return 1\n"},
        same("a.py"),
    )
    assert kinds(result) == [Kind.COMMENT_ONLY]
    assert result[0].qualified_name == "f"
    assert result[0].level == Level.MACHINE


def test_format_only(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def f():\n    return 1+2\n"},
        {"a.py": "def f():\n    return 1 + 2\n"},
        same("a.py"),
    )
    assert kinds(result) == [Kind.FORMAT_ONLY]


def test_blank_line_outside_declaration_is_format_only(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "import os\nx = 1\n"},
        {"a.py": "import os\n\nx = 1\n"},
        same("a.py"),
    )
    assert kinds(result) == [Kind.FORMAT_ONLY]
    assert result[0].qualified_name == ""


def test_moving_call_out_of_block_is_logic(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def f(x):\n    if x:\n        a()\n        b()\n"},
        {"a.py": "def f(x):\n    if x:\n        a()\n    b()\n"},
        same("a.py"),
    )
    assert kinds(result) == [Kind.LOGIC]


def test_public_signature_and_body_are_split(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def f(x):\n    return x\n"},
        {"a.py": "def f(x, y):\n    return x + y\n"},
        same("a.py"),
    )
    assert kinds(result) == [Kind.PUBLIC_INTERFACE, Kind.LOGIC]
    assert required(result[0].new_span).start_line == 1
    assert required(result[1].new_span).start_line == 2
    assert result[0].level == Level.REQUIRED


def test_public_comment_line_after_the_colon_is_body(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def f(x):\n    # a\n    return x\n"},
        {"a.py": "def f(x):\n    # b\n    return x\n"},
        same("a.py"),
    )
    assert Kind.PUBLIC_INTERFACE not in kinds(result)


def test_private_signature_is_logic(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def _f(x):\n    return x\n"},
        {"a.py": "def _f(x, y):\n    return x\n"},
        same("a.py"),
    )
    assert kinds(result) == [Kind.LOGIC]


def test_signature_comment_with_body_change_is_not_public_interface(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def f(\n    x,\n):\n    return x\n"},
        {"a.py": "def f(\n    x,  # note\n):\n    return x + 1\n"},
        same("a.py"),
    )
    assert kinds(result) == [Kind.COMMENT_ONLY, Kind.LOGIC]


def test_new_file_without_old_tree(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        None,
        {"a.py": "import os\n\n\ndef f(x):\n    return x\n"},
        [FilePair(None, "a.py")],
    )
    by_name = {(s.qualified_name, s.kind) for s in result}
    assert ("f", Kind.PUBLIC_INTERFACE) in by_name
    assert ("f", Kind.LOGIC) in by_name
    interface = next(s for s in result if s.kind == Kind.PUBLIC_INTERFACE)
    assert required(interface.new_span).start_line == 4
    assert required(interface.new_span).end_line == 4
    assert interface.old_span is None


def test_removed_public_function(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"},
        {"a.py": "def g():\n    return 2\n"},
        same("a.py"),
    )
    removal = [s for s in result if s.kind == Kind.BEHAVIOR_REMOVAL]
    assert [s.qualified_name for s in removal] == ["f"]
    assert removal[0].new_span is None


def test_removed_private_function_is_logic(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def _f():\n    return 1\n"},
        {"a.py": "x = 1\n"},
        same("a.py"),
    )
    assert Kind.BEHAVIOR_REMOVAL not in kinds(result)


def test_deleted_file(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def f():\n    return 1\n"},
        {},
        [FilePair("a.py", None)],
    )
    assert kinds(result) == [Kind.BEHAVIOR_REMOVAL]
    assert required(result[0].old_span).end_line == 2


def test_deleted_file_without_old_tree_has_no_segment(tmp_path: Path) -> None:
    assert run(tmp_path, None, {}, [FilePair("a.py", None)]) == []


def test_dependency_build(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"pyproject.toml": "[project]\nname = 'a'\n"},
        {"pyproject.toml": "[project]\nname = 'b'\n"},
        same("pyproject.toml"),
    )
    assert kinds(result) == [Kind.DEPENDENCY_BUILD]


def test_lock_file_is_generated(tmp_path: Path) -> None:
    result = run(tmp_path, {"uv.lock": "a\n"}, {"uv.lock": "b\n"}, same("uv.lock"))
    assert kinds(result) == [Kind.GENERATED]
    assert result[0].level == Level.MACHINE


def test_test_file_stays_test_even_for_comment_change(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"tests/test_a.py": "def test_a():\n    assert 1\n"},
        {"tests/test_a.py": "def test_a():\n    # note\n    assert 1\n"},
        same("tests/test_a.py"),
    )
    assert kinds(result) == [Kind.TEST]
    assert result[0].level == Level.CONFIRM


def test_document_is_logic(tmp_path: Path) -> None:
    result = run(tmp_path, {"a.md": "x\n"}, {"a.md": "y\n"}, same("a.md"))
    assert kinds(result) == [Kind.LOGIC]


def test_sensitive_path_logic(tmp_path: Path) -> None:
    config = Config(sensitive=("auth/*",))
    result = run(
        tmp_path,
        {"auth/a.py": "def _f():\n    return 1\n"},
        {"auth/a.py": "def _f():\n    return 2\n"},
        same("auth/a.py"),
        config,
    )
    assert kinds(result) == [Kind.SENSITIVE_LOGIC]
    assert result[0].level == Level.REQUIRED


def test_syntax_error_is_logic(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def f():\n    return 1\n"},
        {"a.py": "def f(:\n    return 1\n"},
        same("a.py"),
    )
    assert kinds(result) == [Kind.LOGIC]


def test_unchanged_file_has_no_segment(tmp_path: Path) -> None:
    files = {"a.py": "x = 1\n"}
    assert run(tmp_path, files, files, same("a.py")) == []


SWIFT_BASE = (
    "import Foundation\n\n"
    "public final class Store {\n"
    "    public func cancelAll(_ ids: [Int]) {\n"
    "        run(ids)\n"
    "    }\n\n"
    "    private func helper(_ x: Int) {\n"
    "        run([x])\n"
    "    }\n"
    "}\n"
)


def swift_result(tmp_path: Path, new: str, old: str = SWIFT_BASE) -> list[Segment]:
    return run(tmp_path, {"S.swift": old}, {"S.swift": new}, same("S.swift"))


def test_swift_body_only_change_is_logic(tmp_path: Path) -> None:
    result = swift_result(tmp_path, SWIFT_BASE.replace("run(ids)", "run(ids.sorted())"))
    assert kinds(result) == [Kind.LOGIC]
    assert result[0].qualified_name == "Store.cancelAll(_:)"


def test_swift_public_signature_change_is_public_interface(tmp_path: Path) -> None:
    result = swift_result(tmp_path, SWIFT_BASE.replace("[Int]) {", "[Int]) async {"))
    assert kinds(result) == [Kind.PUBLIC_INTERFACE]
    assert result[0].qualified_name == "Store.cancelAll(_:)"


def test_swift_added_argument_changes_selector(tmp_path: Path) -> None:
    result = swift_result(tmp_path, SWIFT_BASE.replace("[Int]) {", "[Int], force: Bool) {"))
    assert sorted((s.kind, s.qualified_name) for s in result) == [
        (Kind.BEHAVIOR_REMOVAL, "Store.cancelAll(_:)"),
        (Kind.PUBLIC_INTERFACE, "Store.cancelAll(_:force:)"),
    ]


def test_swift_argument_label_change_is_removal_and_addition(tmp_path: Path) -> None:
    result = swift_result(tmp_path, SWIFT_BASE.replace("_ ids:", "ids:"))
    assert sorted((s.kind, s.qualified_name) for s in result) == [
        (Kind.BEHAVIOR_REMOVAL, "Store.cancelAll(_:)"),
        (Kind.PUBLIC_INTERFACE, "Store.cancelAll(ids:)"),
    ]


def test_swift_private_signature_change_is_logic(tmp_path: Path) -> None:
    result = swift_result(tmp_path, SWIFT_BASE.replace("_ x: Int) {", "_ x: Int) async {"))
    assert kinds(result) == [Kind.LOGIC]
    assert result[0].qualified_name == "Store.helper(_:)"


def test_swift_private_selector_change_is_logic(tmp_path: Path) -> None:
    result = swift_result(tmp_path, SWIFT_BASE.replace("_ x: Int", "_ x: Int, y: Int"))
    assert kinds(result) == [Kind.LOGIC, Kind.LOGIC]


def test_swift_comment_only_change(tmp_path: Path) -> None:
    result = swift_result(
        tmp_path,
        SWIFT_BASE.replace("        run(ids)", "        // note\n        run(ids)"),
    )
    assert kinds(result) == [Kind.COMMENT_ONLY]


def test_swift_public_method_removal_is_behavior_removal(tmp_path: Path) -> None:
    new = SWIFT_BASE.replace(
        "    public func cancelAll(_ ids: [Int]) {\n        run(ids)\n    }\n\n", ""
    )
    result = swift_result(tmp_path, new)
    assert Kind.BEHAVIOR_REMOVAL in kinds(result)
    assert any(s.qualified_name == "Store.cancelAll(_:)" for s in result)


def test_levels() -> None:
    assert set(LEVEL_OF_KIND) == set(Kind)
    assert LEVEL_OF_KIND[Kind.BOT_RESPONSE] == Level.REQUIRED
    assert LEVEL_OF_KIND[Kind.TEST] == Level.CONFIRM
    assert LEVEL_OF_KIND[Kind.GENERATED] == Level.MACHINE
    assert Level.REQUIRED.rank > Level.CONFIRM.rank > Level.MACHINE.rank


def test_load_config(tmp_path: Path) -> None:
    assert load_config(tmp_path) == Config()
    assert Config().exclude == DEFAULT_EXCLUDE
    (tmp_path / ".jigane.toml").write_text(
        'exclude = ["gen/*"]\nsensitive = ["auth/*"]\n', encoding="utf-8"
    )
    assert load_config(tmp_path) == Config(exclude=("gen/*",), sensitive=("auth/*",))


def test_load_config_reads_a_file_with_the_removed_approval_tables(tmp_path: Path) -> None:
    # 承認の規則は外したので、古い [[approval]] の表は読まずに、ほかの鍵だけを読む
    (tmp_path / ".jigane.toml").write_text(
        'exclude = ["gen/*"]\n\n[[approval]]\npaths = ["src/*"]\ncount = 2\n', encoding="utf-8"
    )
    assert load_config(tmp_path) == Config(exclude=("gen/*",))


def build(
    tmp_path: Path,
    old: dict[str, str] | None,
    new: dict[str, str],
    pairs: list[FilePair],
    config: Config | None = None,
) -> JudgmentMap:
    old_root = write(tmp_path / "old", old) if old is not None else None
    new_root = write(tmp_path / "new", new)
    return build_judgment_map(old_root, new_root, pairs, config or Config())


def test_scattered_changes_count_only_changed_lines(tmp_path: Path) -> None:
    body = "".join(f"    a{i} = {i}\n" for i in range(10))
    changed = body.replace("a1 = 1", "a1 = 11").replace("a8 = 8", "a8 = 88")
    result = run(
        tmp_path,
        {"a.py": f"def f():\n{body}"},
        {"a.py": f"def f():\n{changed}"},
        same("a.py"),
    )
    assert len(result) == 1
    assert result[0].old_line_count == 2
    assert result[0].new_line_count == 2
    assert required(result[0].new_span).end_line - required(result[0].new_span).start_line + 1 == 8


def test_declaration_kind(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "class C:\n    def m(self):\n        return 1\nprint(1)\n"},
        {"a.py": "class C:\n    def m(self):\n        return 2\nprint(2)\n"},
        same("a.py"),
    )
    by_name = {s.qualified_name: s.declaration_kind for s in result}
    assert by_name == {"C.m": "method", "": None}


def test_diff_regions_group_by_declaration(tmp_path: Path) -> None:
    result = build(
        tmp_path,
        {"a.py": "def f(x):\n    return x\n\n\ndef g(x):\n    return x\n"},
        {"a.py": "def f(x, y):\n    return x\n\n\ndef g(x, y):\n    return x\n"},
        same("a.py"),
    )
    assert result.mode == "diff"
    assert [r.key for r in result.regions] == [
        "a.py::f::public_interface",
        "a.py::g::public_interface",
    ]
    assert result.regions[0].summary == "公開の関数 `f` のシグネチャを変更"
    assert result.regions[0].line_count == 1
    assert result.regions[0].old_span == Span(1, 1)


def test_diff_region_covers_scattered_signature_lines(tmp_path: Path) -> None:
    merged = build(
        tmp_path / "m",
        {"a.py": "def f(\n    x,\n    z,\n):\n    return x\n"},
        {"a.py": "def f(\n    y,\n    z,\n    w,\n):\n    return x\n"},
        same("a.py"),
    )
    assert len(merged.regions) == 1
    assert merged.regions[0].line_count == 2
    assert merged.regions[0].new_span == Span(2, 4)


def test_region_carries_old_path_on_rename(tmp_path: Path) -> None:
    source = "def f(x):\n    return x\n"
    renamed = build(
        tmp_path / "r",
        {"a.py": source},
        {"b.py": source.replace("x)", "x, y)")},
        [FilePair("a.py", "b.py")],
    )
    plain = build(tmp_path / "p", {"a.py": source}, {"a.py": source + "# c\n"}, same("a.py"))
    feature = build(tmp_path / "f", None, {"a.py": source}, [FilePair(None, "a.py")])
    assert {region.old_path for region in renamed.regions} == {"a.py"}
    assert {region.old_path for region in plain.regions + feature.regions} <= {None}
    assert renamed.to_dict()["regions"][0]["old_path"] == "a.py"


def test_feature_regions_group_by_file(tmp_path: Path) -> None:
    result = build(
        tmp_path,
        None,
        {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"},
        [FilePair(None, "a.py")],
    )
    assert result.mode == "feature"
    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.key == "a.py::::public_interface"
    assert region.summary == "公開: `f`、`g`"
    assert region.line_count == 2


def test_feature_summary_truncates_after_five(tmp_path: Path) -> None:
    source = "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(7))
    result = build(tmp_path, None, {"a.py": source}, [FilePair(None, "a.py")])
    assert result.regions[0].summary == "公開: `f0`、`f1`、`f2`、`f3`、`f4`、ほか 2 件"


def test_feature_summary_with_exactly_five_is_not_truncated(tmp_path: Path) -> None:
    source = "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(5))
    result = build(tmp_path, None, {"a.py": source}, [FilePair(None, "a.py")])
    assert result.regions[0].summary == "公開: `f0`、`f1`、`f2`、`f3`、`f4`"


def test_public_interface_summary_for_each_declaration_kind(tmp_path: Path) -> None:
    result = build(
        tmp_path,
        {"a.py": "class C:\n    pass\n"},
        {
            "a.py": (
                "class C:\n    pass\n\n\nclass D:\n    def m(self):\n        pass\n\n\nV = 1\n"
            )
        },
        same("a.py"),
    )
    summaries = {r.qualified_name: r.summary for r in result.regions}
    assert summaries["D"] == "公開の型 `D` を追加"
    assert summaries["D.m"] == "公開のメソッド `m` を追加"
    assert summaries["V"] == "公開の変数 `V` を追加"


@pytest.mark.parametrize(
    ("name", "source", "types"),
    [
        (
            "a.swift",
            "public enum E {\n    case a\n}\n\npublic struct S {}\n\npublic actor A {}\n",
            ["E", "S", "A"],
        ),
        ("E.java", "public enum E {\n    A;\n}\n", ["E"]),
        (
            "a.cs",
            "public enum E {\n    A\n}\n\npublic struct S {}\n\npublic record R(int X);\n",
            ["E", "S", "R"],
        ),
    ],
)
def test_every_type_declaration_is_summarized_as_a_type(
    tmp_path: Path, name: str, source: str, types: list[str]
) -> None:
    # 宣言の種別 class は、enum、struct、actor、record を含む型の宣言のすべてを指す
    result = build(tmp_path, {name: ""}, {name: source}, same(name))
    summaries = {r.qualified_name: r.summary for r in result.regions}
    for type_name in types:
        assert summaries[type_name] == f"公開の型 `{type_name}` を追加"


def test_appending_an_element_to_an_existing_variable_is_a_change(tmp_path: Path) -> None:
    # 末尾の要素に末尾のカンマがあれば、変更前の行は変わらず、変更前の範囲が空になる
    result = build(
        tmp_path,
        {"a.py": "CHECKS = (\n    first,\n)\n"},
        {"a.py": "CHECKS = (\n    first,\n    second,\n)\n"},
        same("a.py"),
    )
    assert [r.summary for r in result.regions] == ["公開の変数 `CHECKS` を変更"]


def test_dependency_build_summary(tmp_path: Path) -> None:
    changed = build(
        tmp_path / "c",
        {"sub/pyproject.toml": "[project]\nname = 'a'\n"},
        {"sub/pyproject.toml": "[project]\nname = 'b'\n"},
        same("sub/pyproject.toml"),
    )
    assert changed.regions[0].summary == "`pyproject.toml` を変更"
    added = build(
        tmp_path / "a",
        {},
        {"pyproject.toml": "[project]\nname = 'b'\n"},
        [FilePair(None, "pyproject.toml")],
    )
    assert added.regions[0].summary == "`pyproject.toml` を追加"


def test_behavior_removal_summaries(tmp_path: Path) -> None:
    file_removed = build(
        tmp_path / "f",
        {"a.py": "def f():\n    return 1\n"},
        {},
        [FilePair("a.py", None)],
    )
    assert file_removed.regions[0].summary == "ファイル `a.py` を削除"
    function_removed = build(
        tmp_path / "g",
        {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"},
        {"a.py": "def g():\n    return 2\n"},
        same("a.py"),
    )
    summaries = [r.summary for r in function_removed.regions]
    assert "公開の関数 `f` を削除" in summaries


def test_sensitive_logic_summary(tmp_path: Path) -> None:
    config = Config(sensitive=("auth/*",))
    named = build(
        tmp_path / "n",
        {"auth/a.py": "def _f():\n    return 1\n"},
        {"auth/a.py": "def _f():\n    return 2\n"},
        same("auth/a.py"),
        config,
    )
    assert named.regions[0].summary == "センシティブなパスのロジックを変更（`_f`）"
    unnamed = build(
        tmp_path / "u",
        {"auth/a.py": "print(1)\n"},
        {"auth/a.py": "print(2)\n"},
        same("auth/a.py"),
        config,
    )
    assert unnamed.regions[0].summary == "センシティブなパスのロジックを変更"


def test_confirm_and_machine_segments_make_no_region(tmp_path: Path) -> None:
    result = build(
        tmp_path,
        {"a.py": "print(1)\n"},
        {"a.py": "print(2)\n"},
        same("a.py"),
    )
    assert result.regions == []


def test_content_hash_follows_content_only_in_region(tmp_path: Path) -> None:
    def hash_of(root: str, signature: str, tail: str) -> str:
        result = build(
            tmp_path / root,
            {"a.py": "def f(x):\n    return x\n\n\nz = 0\n"},
            {"a.py": f"{signature}\n    return x\n\n\nz = {tail}\n"},
            same("a.py"),
        )
        region = next(r for r in result.regions if r.qualified_name == "f")
        return region.content_hash

    base = hash_of("base", "def f(x, y):", "1")
    assert hash_of("same", "def f(x, y):", "2") == base
    assert hash_of("other", "def f(x, z):", "1") != base


def test_line_counts(tmp_path: Path) -> None:
    result = build(
        tmp_path,
        {"a.py": "def f(x):\n    return x\n", "b.py": "x = 1\ny = 2\n"},
        {"a.py": "def f(x, y):\n    return x\n"},
        [FilePair("a.py", "a.py"), FilePair("b.py", None)],
    )
    counts = result.line_counts()
    assert counts.by_kind["public_interface"] == 1
    assert counts.by_kind["behavior_removal"] == 2
    assert counts.by_level["required"] == 3
    assert counts.by_level["machine"] == 0


def test_line_counts_by_file_sum_to_level_totals(tmp_path: Path) -> None:
    result = build(
        tmp_path,
        {"a.py": "def f(x):\n    return x\n", "b.py": "x = 1\ny = 2\n", "c.py": "# a\n"},
        {"a.py": "def f(x, y):\n    return x\n", "c.py": "# b\n"},
        [FilePair("a.py", "a.py"), FilePair("b.py", None), FilePair("c.py", "c.py")],
    )
    counts = result.line_counts()
    by_file = file_line_counts(result.segments)
    assert by_file["b.py"]["required"] == 2
    assert by_file["a.py"]["required"] == 1
    for level, total in counts.by_level.items():
        assert sum(levels[level] for levels in by_file.values()) == total


def test_to_dict_is_json_serializable(tmp_path: Path) -> None:
    result = build(
        tmp_path,
        {"a.py": "def f(x):\n    return x\n"},
        {"a.py": "def f(x, y):\n    return x + y\n"},
        same("a.py"),
    )
    data = json.loads(json.dumps(result.to_dict()))
    assert data["mode"] == "diff"
    assert data["segments"][0]["kind"] == "public_interface"
    assert data["segments"][0]["new_span"] == {"start_line": 1, "end_line": 1}
    assert data["regions"][0]["key"] == "a.py::f::public_interface"


CS_OLD = (
    "public class Account\n"
    "{\n"
    "    // balance\n"
    "    public int Balance(int a)\n"
    "    {\n"
    "        return a;\n"
    "    }\n"
    "\n"
    "    int Helper(int a)\n"
    "    {\n"
    "        return a;\n"
    "    }\n"
    "}\n"
)


def cs_segments(tmp_path: Path, new: str) -> dict[str, Kind]:
    result = run(tmp_path, {"A.cs": CS_OLD}, {"A.cs": new}, same("A.cs"))
    return {s.qualified_name: s.kind for s in result}


def test_csharp_body_change_is_logic(tmp_path: Path) -> None:
    new = CS_OLD.replace("return a;\n    }\n\n    int", "return a + 1;\n    }\n\n    int")
    assert cs_segments(tmp_path, new) == {"Account.Balance": Kind.LOGIC}


def test_csharp_public_signature_change_is_public_interface(tmp_path: Path) -> None:
    new = CS_OLD.replace("Balance(int a)", "Balance(long a)")
    assert cs_segments(tmp_path, new) == {"Account.Balance": Kind.PUBLIC_INTERFACE}


def test_csharp_private_signature_change_is_logic(tmp_path: Path) -> None:
    new = CS_OLD.replace("Helper(int a)", "Helper(long a)")
    assert cs_segments(tmp_path, new) == {"Account.Helper": Kind.LOGIC}


def test_csharp_comment_only(tmp_path: Path) -> None:
    new = CS_OLD.replace("// balance", "// the balance")
    assert set(cs_segments(tmp_path, new).values()) == {Kind.COMMENT_ONLY}


def test_csharp_public_method_removal_is_behavior_removal(tmp_path: Path) -> None:
    new = CS_OLD.replace("    public int Balance(int a)\n    {\n        return a;\n    }\n\n", "")
    assert cs_segments(tmp_path, new)["Account.Balance"] == Kind.BEHAVIOR_REMOVAL


JAVA_OLD = (
    "public class A {\n"
    "    public int pub(int a) {\n"
    "        return a + 1;\n"
    "    }\n"
    "    private int priv(int a) {\n"
    "        return a;\n"
    "    }\n"
    "}\n"
)


def java(old: str, new: str, tmp_path: Path) -> list[Segment]:
    return run(tmp_path, {"A.java": old}, {"A.java": new}, same("A.java"))


def test_java_body_change_is_logic(tmp_path: Path) -> None:
    result = java(JAVA_OLD, JAVA_OLD.replace("a + 1", "a + 2"), tmp_path)
    assert kinds(result) == [Kind.LOGIC]
    assert result[0].qualified_name == "A.pub"


def test_java_public_signature_change_is_public_interface(tmp_path: Path) -> None:
    result = java(JAVA_OLD, JAVA_OLD.replace("pub(int a)", "pub(long a)"), tmp_path)
    assert kinds(result) == [Kind.PUBLIC_INTERFACE]
    assert result[0].qualified_name == "A.pub"


def test_java_private_signature_change_is_logic(tmp_path: Path) -> None:
    result = java(JAVA_OLD, JAVA_OLD.replace("priv(int a)", "priv(long a)"), tmp_path)
    assert kinds(result) == [Kind.LOGIC]
    assert result[0].qualified_name == "A.priv"


def test_java_comment_only(tmp_path: Path) -> None:
    result = java(
        JAVA_OLD,
        JAVA_OLD.replace("        return a + 1;", "        // n\n        return a + 1;"),
        tmp_path,
    )
    assert kinds(result) == [Kind.COMMENT_ONLY]


def test_java_public_method_removal_is_behavior_removal(tmp_path: Path) -> None:
    new = "public class A {\n    private int priv(int a) {\n        return a;\n    }\n}\n"
    result = java(JAVA_OLD, new, tmp_path)
    assert Kind.BEHAVIOR_REMOVAL in kinds(result)
    removed = [s for s in result if s.kind == Kind.BEHAVIOR_REMOVAL]
    assert removed[0].qualified_name == "A.pub"


OVERLOADS = (
    "public class A {\n"
    "    public int f(int a) {\n"
    "        return a;\n"
    "    }\n"
    "    private int f(String a) {\n"
    "        return 0;\n"
    "    }\n"
    "}\n"
)


def test_java_overload_body_change_in_one_is_logic(tmp_path: Path) -> None:
    result = java(OVERLOADS, OVERLOADS.replace("return 0;", "return 1;"), tmp_path)
    assert kinds(result) == [Kind.LOGIC]
    assert result[0].qualified_name == "A.f"


def test_java_private_overload_signature_change_is_logic(tmp_path: Path) -> None:
    result = java(OVERLOADS, OVERLOADS.replace("f(String a)", "f(long a)"), tmp_path)
    assert result[0].qualified_name == "A.f"
    assert kinds(result) == [Kind.LOGIC]


def test_java_public_overload_signature_change_is_public_interface(tmp_path: Path) -> None:
    result = java(OVERLOADS, OVERLOADS.replace("f(int a)", "f(long a)"), tmp_path)
    assert kinds(result) == [Kind.PUBLIC_INTERFACE]


def test_java_removing_all_overloads_with_public_is_behavior_removal(tmp_path: Path) -> None:
    result = java(OVERLOADS, "public class A {\n}\n", tmp_path)
    assert kinds(result) == [Kind.BEHAVIOR_REMOVAL]


CS_IF = (
    "public class A\n"
    "{\n"
    "#if X\n"
    "    public int F(int a)\n"
    "    {\n"
    "        return a;\n"
    "    }\n"
    "#else\n"
    "    private int F(long a)\n"
    "    {\n"
    "        return 0;\n"
    "    }\n"
    "#endif\n"
    "}\n"
)


def cs_if(tmp_path: Path, new: str) -> list[Segment]:
    return run(tmp_path, {"A.cs": CS_IF}, {"A.cs": new}, same("A.cs"))


def test_csharp_if_else_private_arm_signature_is_logic(tmp_path: Path) -> None:
    result = cs_if(tmp_path, CS_IF.replace("F(long a)", "F(short a)"))
    assert kinds(result) == [Kind.LOGIC]


def test_csharp_if_else_public_arm_signature_is_public_interface(tmp_path: Path) -> None:
    result = cs_if(tmp_path, CS_IF.replace("F(int a)", "F(char a)"))
    assert kinds(result) == [Kind.PUBLIC_INTERFACE]


def test_csharp_if_else_public_arm_body_is_logic(tmp_path: Path) -> None:
    result = cs_if(tmp_path, CS_IF.replace("return a;", "return 2;"))
    assert kinds(result) == [Kind.LOGIC]


def declarators(tmp_path: Path, file: str, old: str, new: str) -> list[Segment]:
    return run(tmp_path, {file: old}, {file: new}, same(file))


JAVA_FIELDS = "public class A {\n    %s int a = 1, b = 2;\n}\n"
CS_FIELDS = "public class A\n{\n    %s int a = 1, b = 2;\n}\n"
SWIFT_CASES = "public enum E {\n    case a, b\n}\n"


def test_declarators_value_change_is_logic(tmp_path: Path) -> None:
    for file, template in (("A.java", JAVA_FIELDS), ("A.cs", CS_FIELDS)):
        old = template % "private"
        for replaced in ("a = 1", "b = 2"):
            new = old.replace(replaced, replaced[:-1] + "9")
            result = declarators(tmp_path / file / replaced, file, old, new)
            assert kinds(result) == [Kind.LOGIC], (file, replaced)


def test_declarators_type_change_of_public_is_public_interface(tmp_path: Path) -> None:
    for file, template in (("A.java", JAVA_FIELDS), ("A.cs", CS_FIELDS)):
        old = template % "public"
        new = old.replace("int", "long")
        result = declarators(tmp_path / file, file, old, new)
        assert kinds(result) == [Kind.PUBLIC_INTERFACE], file


def test_declarators_added_and_removed_are_classified(tmp_path: Path) -> None:
    for file, template in (("A.java", JAVA_FIELDS), ("A.cs", CS_FIELDS)):
        old = template % "public"
        added = declarators(
            tmp_path / file / "add", file, old, old.replace("b = 2", "b = 2, c = 3")
        )
        assert kinds(added) == [Kind.PUBLIC_INTERFACE], file
        removed = declarators(tmp_path / file / "del", file, old, old.replace(", b = 2", ""))
        assert kinds(removed) == [Kind.PUBLIC_INTERFACE], file
        removed_first = declarators(
            tmp_path / file / "del1", file, old, old.replace("a = 1, ", "")
        )
        assert Kind.BEHAVIOR_REMOVAL in kinds(removed_first), file


def test_swift_case_declarators_are_classified(tmp_path: Path) -> None:
    added = declarators(
        tmp_path / "add", "A.swift", SWIFT_CASES, SWIFT_CASES.replace("a, b", "a, b, c")
    )
    assert kinds(added) == [Kind.PUBLIC_INTERFACE]
    removed = declarators(
        tmp_path / "del", "A.swift", SWIFT_CASES, SWIFT_CASES.replace("a, b", "a")
    )
    assert kinds(removed) == [Kind.PUBLIC_INTERFACE]


SWIFT_VARS = "public struct T {\n    %s var a: Int = 1, b: Int = 2\n}\n"


def test_swift_var_declarators_are_classified(tmp_path: Path) -> None:
    private = SWIFT_VARS % "private"
    value = declarators(
        tmp_path / "v", "A.swift", private, private.replace("a: Int = 1", "a: Int = 3")
    )
    assert kinds(value) == [Kind.LOGIC]
    public = SWIFT_VARS % "public"
    typed = declarators(tmp_path / "t", "A.swift", public, public.replace("a: Int", "a: Double"))
    assert kinds(typed) == [Kind.PUBLIC_INTERFACE]


def span(start: int, end: int) -> Span:
    return Span(start, end)


def segment_at(path: str, start: int, end: int, kind: Kind, count: int | None = None) -> Segment:
    return Segment(
        path,
        path,
        span(start, end),
        span(start, end),
        "f",
        kind,
        LEVEL_OF_KIND[kind],
        count or end - start + 1,
        count or end - start + 1,
        "function",
        ChangeKind.CHANGED,
    )


def test_apply_bot_touches_raises_confirm_to_bot_response() -> None:
    result = apply_bot_touches(
        [segment_at("a.py", 3, 5, Kind.LOGIC)],
        [BotTouch(11, "a.py", frozenset({4}))],
    )
    assert result[0].kind == Kind.BOT_RESPONSE
    assert result[0].level == Level.REQUIRED
    assert result[0].bot_comment_ids == (11,)


def test_apply_bot_touches_keeps_kind_of_required_segment() -> None:
    result = apply_bot_touches(
        [segment_at("a.py", 3, 5, Kind.SENSITIVE_LOGIC)],
        [BotTouch(12, "a.py", frozenset({3})), BotTouch(11, "a.py", frozenset({5}))],
    )
    assert result[0].kind == Kind.SENSITIVE_LOGIC
    assert result[0].bot_comment_ids == (11, 12)


def test_apply_bot_touches_ignores_other_path_and_lines() -> None:
    segments = [segment_at("a.py", 3, 5, Kind.LOGIC)]
    result = apply_bot_touches(
        segments,
        [BotTouch(1, "b.py", frozenset({4})), BotTouch(2, "a.py", frozenset({6}))],
    )
    assert result == segments


def test_bot_response_line_counts_not_doubled() -> None:
    segments = [
        segment_at("a.py", 3, 5, Kind.LOGIC, count=2),
        segment_at("a.py", 9, 9, Kind.SENSITIVE_LOGIC),
    ]
    touched = apply_bot_touches(
        segments,
        [BotTouch(1, "a.py", frozenset({4})), BotTouch(2, "a.py", frozenset({9}))],
    )
    counts = JudgmentMap(StoredMode.DIFF, touched).line_counts()
    assert counts.by_kind["bot_response"] == 2
    assert counts.by_kind["logic"] == 0
    assert counts.by_kind["sensitive_logic"] == 1
    assert sum(counts.by_kind.values()) == 3
    assert sum(counts.by_level.values()) == 3


def test_bot_response_region_summary_and_ids(tmp_path: Path) -> None:
    new_root = write(tmp_path / "new", {"a.py": "def f():\n    return 1\n"})
    old_root = write(tmp_path / "old", {"a.py": "def f():\n    return 0\n"})
    pairs = same("a.py")
    plain = build_judgment_map(old_root, new_root, pairs, Config())
    assert plain.regions == []
    touched = build_judgment_map(
        old_root, new_root, pairs, Config(), [BotTouch(5, "a.py", frozenset({2}))]
    )
    region = touched.regions[0]
    assert region.kind == Kind.BOT_RESPONSE
    assert region.bot_comment_ids == (5,)
    assert region.summary == "ボットの指摘 1 件の後の変更（`f`）"
    assert touched.to_dict()["regions"][0]["bot_comment_ids"] == [5]


def test_required_region_summary_notes_bot_comments(tmp_path: Path) -> None:
    new_root = write(tmp_path / "new", {"a.py": "def f():\n    return 1\n"})
    old_root = write(tmp_path / "old", {"a.py": "def f():\n    return 0\n"})
    config = Config(sensitive=("a.py",))
    touched = build_judgment_map(
        old_root,
        new_root,
        same("a.py"),
        config,
        [BotTouch(5, "a.py", frozenset({2}))],
    )
    region = touched.regions[0]
    assert region.kind == Kind.SENSITIVE_LOGIC
    assert region.bot_comment_ids == (5,)
    assert region.summary.endswith("。ボットの指摘 1 件の後の変更を含む")


def summaries_of(tmp_path: Path, name: str, old: str, new: str) -> dict[str, tuple[str, str]]:
    result = build(tmp_path, {name: old}, {name: new}, same(name))
    return {r.qualified_name: (r.kind, r.summary) for r in result.regions}


def test_csharp_internal_constructor_in_internal_class_is_internal_interface(
    tmp_path: Path,
) -> None:
    old = "internal class Importer\n{\n}\n"
    new = "internal class Importer\n{\n    internal Importer(int a)\n    {\n    }\n}\n"
    found = summaries_of(tmp_path, "A.cs", old, new)
    assert found["Importer.Importer"] == (
        Kind.INTERNAL_INTERFACE,
        "内部公開のメソッド `Importer` を追加",
    )


def test_csharp_public_method_in_public_class_stays_public_interface(tmp_path: Path) -> None:
    old = "public class Importer\n{\n}\n"
    new = "public class Importer\n{\n    public void Run()\n    {\n    }\n}\n"
    found = summaries_of(tmp_path, "A.cs", old, new)
    assert found["Importer.Run"] == (Kind.PUBLIC_INTERFACE, "公開のメソッド `Run` を追加")


def test_swift_default_internal_func_is_internal_interface(tmp_path: Path) -> None:
    old = "public struct S {\n}\n"
    new = "public struct S {\n    func run() {\n    }\n}\n"
    found = summaries_of(tmp_path, "a.swift", old, new)
    assert found["S.run()"][0] == Kind.INTERNAL_INTERFACE
    assert found["S.run()"][1].startswith("内部公開の")


def test_java_package_private_method_is_internal_interface(tmp_path: Path) -> None:
    old = "public class A {\n}\n"
    new = "public class A {\n    int run(int a) {\n        return a;\n    }\n}\n"
    found = summaries_of(tmp_path, "A.java", old, new)
    assert found["A.run"] == (Kind.INTERNAL_INTERFACE, "内部公開のメソッド `run` を追加")


def test_java_package_private_signature_change_is_internal_interface(tmp_path: Path) -> None:
    old = "public class A {\n    int run(int a) {\n        return a;\n    }\n}\n"
    new = old.replace("run(int a)", "run(long a)")
    found = summaries_of(tmp_path, "A.java", old, new)
    assert found["A.run"] == (
        Kind.INTERNAL_INTERFACE,
        "内部公開のメソッド `run` のシグネチャを変更",
    )


def test_removed_internal_method_summary(tmp_path: Path) -> None:
    old = "public class A {\n    int run(int a) {\n        return a;\n    }\n}\n"
    new = "public class A {\n}\n"
    found = summaries_of(tmp_path, "A.java", old, new)
    assert found["A.run"] == (Kind.BEHAVIOR_REMOVAL, "内部公開のメソッド `run` を削除")


def change_kinds(segments: list[Segment]) -> list[ChangeKind]:
    return [s.change_kind for s in segments]


def test_appending_to_existing_variable_is_changed(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "CHECKS = (\n    first,\n)\n"},
        {"a.py": "CHECKS = (\n    first,\n    second,\n)\n"},
        same("a.py"),
    )
    assert set(change_kinds(result)) == {ChangeKind.CHANGED}


def test_new_function_is_added(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def f():\n    return 1\n"},
        {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"},
        same("a.py"),
    )
    assert {s.change_kind for s in result if s.qualified_name == "g"} == {ChangeKind.ADDED}


def test_removed_function_is_removed(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"},
        {"a.py": "def f():\n    return 1\n"},
        same("a.py"),
    )
    assert {s.change_kind for s in result if s.qualified_name == "g"} == {ChangeKind.REMOVED}


def test_new_java_overload_is_added(tmp_path: Path) -> None:
    new = OVERLOADS.replace(
        "    private int f(String a) {",
        "    public int f(long a) {\n        return 2;\n    }\n    private int f(String a) {",
    )
    result = java(OVERLOADS, new, tmp_path)
    assert set(change_kinds(result)) == {ChangeKind.ADDED}


def test_java_overload_signature_change_is_changed(tmp_path: Path) -> None:
    result = java(OVERLOADS, OVERLOADS.replace("f(int a)", "f(long a)"), tmp_path)
    assert set(change_kinds(result)) == {ChangeKind.CHANGED}


def test_csharp_new_overload_is_added(tmp_path: Path) -> None:
    old = "public class A\n{\n    public int F(int a)\n    {\n        return a;\n    }\n}\n"
    new = old.replace(
        "    }\n}\n", "    }\n\n    public int F(string a)\n    {\n        return 0;\n    }\n}\n"
    )
    result = run(tmp_path, {"A.cs": old}, {"A.cs": new}, same("A.cs"))
    assert {s.change_kind for s in result if s.qualified_name == "A.F"} == {ChangeKind.ADDED}


@pytest.mark.parametrize(
    ("old_span", "new_span", "expected"),
    [
        (None, {"start_line": 1, "end_line": 2}, ChangeKind.ADDED),
        ({"start_line": 1, "end_line": 2}, None, ChangeKind.REMOVED),
        ({"start_line": 1, "end_line": 2}, {"start_line": 1, "end_line": 3}, ChangeKind.CHANGED),
    ],
)
def test_map_v1_upgrade_fills_change_kind_from_the_spans(
    old_span: dict[str, int] | None, new_span: dict[str, int] | None, expected: ChangeKind
) -> None:
    stored = {
        "old_path": "a.py",
        "new_path": "a.py",
        "old_span": old_span,
        "new_span": new_span,
        "qualified_name": "f",
        "kind": "logic",
        "level": "confirm",
        "old_line_count": 2,
        "new_line_count": 3,
        "declaration_kind": "function",
    }
    old_map = {"mode": "diff", "segments": [stored], "regions": [], "line_counts": {}}

    upgraded = _upgrade_v1(old_map)

    # static_finding_ids は版 4 の欄で、版 1 の移しの範囲ではない
    segment = {**upgraded["segments"][0], "static_finding_ids": []}
    assert Segment.from_dict(segment).change_kind is expected


def test_comment_added_outside_any_declaration_is_comment_only(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "X = 1\n\ndef f():\n    return 1\n"},
        {"a.py": "X = 1\n# note\n\ndef f():\n    return 1\n"},
        same("a.py"),
    )
    assert [(s.kind, s.qualified_name, s.old_span, s.new_span) for s in result] == [
        (Kind.COMMENT_ONLY, "", None, Span(start_line=2, end_line=2))
    ]


def test_public_variable_change_is_public_interface(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "X = 1\n\ndef f():\n    return 1\n"},
        {"a.py": "X = 2\n\ndef f():\n    return 1\n"},
        same("a.py"),
    )
    assert [(s.kind, s.qualified_name) for s in result] == [(Kind.PUBLIC_INTERFACE, "X")]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("def f(:\n    return 1\n", "def f():\n    return 1\n"),
        ("def f():\n    return 1\n", "def f(:\n    return 1\n"),
    ],
)
def test_syntax_error_on_either_side_is_one_logic_segment_without_a_name(
    tmp_path: Path, old: str, new: str
) -> None:
    result = run(tmp_path, {"a.py": old}, {"a.py": new}, same("a.py"))
    assert [(s.kind, s.qualified_name) for s in result] == [(Kind.LOGIC, "")]


def test_two_declarations_are_classified_apart(tmp_path: Path) -> None:
    result = run(
        tmp_path,
        {"a.py": "def f():\n    return 1\n\ndef g():\n    return 1\n"},
        {"a.py": "def f():\n    return 2\n\ndef g():\n    # c\n    return 1\n"},
        same("a.py"),
    )
    assert [(s.kind, s.qualified_name) for s in result] == [
        (Kind.LOGIC, "f"),
        (Kind.COMMENT_ONLY, "g"),
    ]


def finding(
    finding_id: str, path: str, start: int, end: int, *, raises: bool = True
) -> FindingTouch:
    return FindingTouch(finding_id, path, start, end, raises)


def test_apply_static_findings_raises_confirm_for_error_and_warning() -> None:
    overlay = apply_static_findings(
        [segment_at("a.py", 3, 5, Kind.LOGIC)], [finding("S1", "a.py", 5, 9)]
    )
    assert overlay.segments[0].kind == Kind.STATIC_FINDING
    assert overlay.segments[0].level == Level.REQUIRED
    assert overlay.segments[0].static_finding_ids == ("S1",)
    assert overlay.outside_count == 0


def test_apply_static_findings_only_labels_for_note_and_none() -> None:
    overlay = apply_static_findings(
        [segment_at("a.py", 3, 5, Kind.LOGIC)], [finding("S1", "a.py", 4, 4, raises=False)]
    )
    assert overlay.segments[0].kind == Kind.LOGIC
    assert overlay.segments[0].level == Level.CONFIRM
    assert overlay.segments[0].static_finding_ids == ("S1",)
    assert overlay.outside_count == 0


def test_apply_static_findings_raises_when_any_finding_raises() -> None:
    overlay = apply_static_findings(
        [segment_at("a.py", 3, 5, Kind.LOGIC)],
        [finding("S2", "a.py", 4, 4, raises=False), finding("S1", "a.py", 3, 3)],
    )
    assert overlay.segments[0].kind == Kind.STATIC_FINDING
    assert overlay.segments[0].static_finding_ids == ("S1", "S2")


def test_apply_static_findings_orders_ids_by_number() -> None:
    overlay = apply_static_findings(
        [segment_at("a.py", 3, 5, Kind.LOGIC)],
        [finding("S10", "a.py", 3, 3), finding("S2", "a.py", 4, 4)],
    )
    assert overlay.segments[0].static_finding_ids == ("S2", "S10")


def test_apply_static_findings_keeps_kind_of_required_segment() -> None:
    overlay = apply_static_findings(
        [segment_at("a.py", 3, 5, Kind.SENSITIVE_LOGIC)], [finding("S1", "a.py", 3, 3)]
    )
    assert overlay.segments[0].kind == Kind.SENSITIVE_LOGIC
    assert overlay.segments[0].static_finding_ids == ("S1",)


def test_apply_static_findings_counts_findings_outside_the_diff() -> None:
    segments = [segment_at("a.py", 3, 5, Kind.LOGIC)]
    overlay = apply_static_findings(
        segments,
        [
            finding("S1", "b.py", 4, 4),
            finding("S2", "a.py", 6, 8),
            finding("S3", "a.py", 1, 2),
            finding("S4", "a.py", 5, 5),
        ],
    )
    assert overlay.outside_count == 3
    assert overlay.segments[0].static_finding_ids == ("S4",)


def test_apply_static_findings_counts_a_finding_over_two_segments_once() -> None:
    segments = [segment_at("a.py", 3, 4, Kind.LOGIC), segment_at("a.py", 7, 8, Kind.LOGIC)]
    overlay = apply_static_findings(segments, [finding("S1", "a.py", 1, 20)])
    assert overlay.outside_count == 0
    assert [s.static_finding_ids for s in overlay.segments] == [("S1",), ("S1",)]


def test_static_finding_line_counts_not_doubled() -> None:
    segments = [
        segment_at("a.py", 3, 5, Kind.LOGIC, count=2),
        segment_at("a.py", 9, 9, Kind.SENSITIVE_LOGIC),
    ]
    overlay = apply_static_findings(
        segments,
        [finding("S1", "a.py", 4, 4), finding("S2", "a.py", 3, 5), finding("S3", "a.py", 9, 9)],
    )
    counts = JudgmentMap(StoredMode.DIFF, list(overlay.segments)).line_counts()
    assert counts.by_kind["static_finding"] == 2
    assert counts.by_kind["logic"] == 0
    assert counts.by_kind["sensitive_logic"] == 1
    assert sum(counts.by_kind.values()) == 3
    assert sum(counts.by_level.values()) == 3


def test_region_summary_for_policy_lists_only_the_finding_count(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x\n" * 6, encoding="utf-8")
    overlay = apply_static_findings(
        [segment_at("a.py", 3, 5, Kind.LOGIC)],
        [finding("S1", "a.py", 4, 4), finding("S2", "a.py", 5, 5)],
    )
    regions = build_regions(overlay.segments, tmp_path, tmp_path)
    assert regions[0].kind == Kind.STATIC_FINDING
    assert regions[0].summary == "静的検査の指摘 2 件（`f`）"
    assert "S1" not in json.dumps(regions[0].to_dict(), ensure_ascii=False)
