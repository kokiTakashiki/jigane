from pathlib import Path

import pytest
from gates_support import write_gates_file

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_GATE_VIOLATION, EXIT_OK
from jigane.flaky import gates as gates_module
from jigane.flaky.catalog import (
    ANNOTATION_EXPLANATION,
    ANNOTATION_ID,
    CatalogError,
    Equivalent,
    EquivalentState,
    Item,
    load_catalog,
    parse_catalog,
)
from jigane.flaky.detector import Detection, QueryDetection, detect_source
from jigane.flaky.explanation import explain
from jigane.flaky.gates import load_gates, update_gates
from jigane.gates.entry import Gates
from jigane.gates.line_scope import AllLines, ChangedLines, LineScope
from jigane.judgment_map.allow_annotation import split_rows
from jigane.judgment_map.classification import ChangeKind, Segment
from jigane.judgment_map.kinds import LEVEL_OF_KIND, Kind
from jigane.languages.base import Span
from jigane.languages.python import PythonAdapter
from jigane.reviews.lifecycle import create_feature_review

FIXTURES = Path(__file__).parent / "fixtures" / "flaky" / "python"
CATALOG = load_catalog()
LANGUAGES = frozenset({"python", "java"})
ADAPTER = PythonAdapter()


def _detect(text: str, scope: LineScope | None = None) -> list[tuple[str, int]]:
    scope = AllLines() if scope is None else scope
    found = detect_source("tests/test_x.py", text.encode(), ADAPTER, CATALOG, scope)
    return [(d.item_id, d.line) for d in found]


@pytest.mark.parametrize("item", [i for i in CATALOG if i.queries], ids=lambda i: i.id)
def test_fixtures(item: Item) -> None:
    name = item.id.removeprefix("FLK-").lower()
    bad = (FIXTURES / f"{name}-bad.txt").read_text(encoding="utf-8")
    good = (FIXTURES / f"{name}-good.txt").read_text(encoding="utf-8")
    assert item.id in {i for i, _ in _detect(bad)}
    assert _detect(good) == []


def test_catalog_has_twelve_items() -> None:
    assert len(CATALOG) == 12


@pytest.mark.parametrize(
    "text",
    [
        "[[item]]\nid='FLK-X'\n",
        "[[item]]\nid='FLK-X'\ncause='Time'\nconfidence='direct'\nsource='s'\nlegitimate='l'\n",
        "not toml [",
        "[[item]]\nid='bad'\ncause='Time'\nconfidence='direct'\nsource='s'\nlegitimate='l'\n",
        "[[item]]\nid='FLK-X'\ncause='Nope'\nconfidence='direct'\nsource='s'\nlegitimate='l'\n",
        "[[item]]\nid='FLK-X'\ncause='Time'\nconfidence='maybe'\nsource='s'\nlegitimate='l'\n",
    ],
)
def test_catalog_rejects_malformed(text: str) -> None:
    with pytest.raises(CatalogError):
        parse_catalog(text, {}, LANGUAGES)


def test_duplicate_id_rejected() -> None:
    entry = _ENTRY
    with pytest.raises(CatalogError):
        parse_catalog(entry + entry, {}, LANGUAGES)


_ENTRY_HEAD = (
    "[[item]]\nid='FLK-X'\ncause='Time'\nconfidence='direct'\n"
    "source='s'\nlegitimate='l'\n"
    "concern='c。'\nviolation='v。'\ndirection='d。'\n"
)
_ENTRY = (
    _ENTRY_HEAD
    + "[item.equivalent.python]\nstate='found'\nrules=['ruff X1']\nnote='n'\n"
    + "[item.equivalent.java]\nstate='unverified'\n"
)


@pytest.mark.parametrize(
    "equivalent",
    [
        "",
        "[item.equivalent.python]\nstate='none'\n",
        "[item.equivalent.python]\nstate='none'\n[item.equivalent.java]\n",
        "[item.equivalent.python]\nstate='none'\n[item.equivalent.java]\nstate='maybe'\n",
        (
            "[item.equivalent.python]\nstate='none'\n[item.equivalent.java]\nstate='none'\n"
            "[item.equivalent.cobol]\nstate='none'\n"
        ),
        "[item.equivalent.python]\nstate='none'\n[item.equivalent.java]\nstate='none'\nx='y'\n",
        (
            "[item.equivalent.python]\nstate='none'\n[item.equivalent.java]\nstate='none'\n"
            "rules=[' ']\n"
        ),
        # 状態と中身の矛盾
        "[item.equivalent.python]\nstate='found'\nnote='n'\n[item.equivalent.java]\nstate='none'\n",
        (
            "[item.equivalent.python]\nstate='found'\nrules=['r']\n"
            "[item.equivalent.java]\nstate='none'\n"
        ),
        (
            "[item.equivalent.python]\nstate='none'\nrules=['r']\n"
            "[item.equivalent.java]\nstate='none'\n"
        ),
        (
            "[item.equivalent.python]\nstate='unverified'\nnote='n'\n"
            "[item.equivalent.java]\nstate='none'\n"
        ),
    ],
    ids=[
        "table missing",
        "language missing",
        "state missing",
        "unknown state",
        "unknown language",
        "unknown key",
        "blank rule",
        "found without rules",
        "found without note",
        "none with rules",
        "unverified with note",
    ],
)
def test_invalid_equivalent_rejected(equivalent: str) -> None:
    with pytest.raises(CatalogError, match="equivalent"):
        parse_catalog(_ENTRY_HEAD + equivalent, {}, LANGUAGES)


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        (
            "state='found'\nrules=['ruff X1', 'ruff X2']\nnote='n'\n",
            Equivalent(EquivalentState.FOUND, ("ruff X1", "ruff X2"), "n"),
        ),
        ("state='none'\n", Equivalent(EquivalentState.NONE)),
        ("state='none'\nnote='n'\n", Equivalent(EquivalentState.NONE, (), "n")),
        ("state='unverified'\n", Equivalent(EquivalentState.UNVERIFIED)),
    ],
    ids=["found", "none", "none with note", "unverified"],
)
def test_equivalent_state_is_read_and_reaches_the_query(table: str, expected: Equivalent) -> None:
    text = _ENTRY_HEAD + f"[item.equivalent.python]\n{table}[item.equivalent.java]\nstate='none'\n"
    queries = {"python": "[[query]]\nid='FLK-X'\nquery='(x)'\n"}
    [item] = parse_catalog(text, queries, LANGUAGES)
    assert [spec.equivalent for spec in item.queries] == [expected]


def test_real_catalog_queries_carry_one_equivalent_per_item_and_language() -> None:
    for item in CATALOG:
        per_language: dict[str, set[Equivalent]] = {}
        for spec in item.queries:
            per_language.setdefault(spec.language, set()).add(spec.equivalent)
        assert all(len(found) == 1 for found in per_language.values())


def test_real_catalog_equivalent_states() -> None:
    state = {
        (item.id, spec.language): spec.equivalent.state
        for item in CATALOG
        for spec in item.queries
    }
    assert state[("FLK-TIME", "python")] is EquivalentState.FOUND
    assert state[("FLK-NETWORK", "python")] is EquivalentState.NONE
    assert state[("FLK-TIME", "java")] is EquivalentState.UNVERIFIED
    assert state[("FLK-TIME", "swift")] is EquivalentState.NONE


@pytest.mark.parametrize(
    ("query_texts", "message"),
    [
        ({"cobol": "[[query]]\nid='FLK-X'\nquery='(x)'\n"}, "言語アダプタに無い"),
        ({"python": "[[query]]\nid='FLK-X'\nquery='(x)'\ncheck='nope'\n"}, "知らない check"),
        ({"python": "[[query]]\nid='FLK-Y'\nquery='(x)'\n"}, "目録に無い id"),
        (
            {"python": "[[query]]\nid='FLK-X'\nquery='(x)'\n" * 2},
            "同じ言語で id と check が重複",
        ),
    ],
)
def test_queries_rejects_malformed(query_texts: dict[str, str], message: str) -> None:
    with pytest.raises(CatalogError, match=message):
        parse_catalog(_ENTRY, query_texts, LANGUAGES)


def test_same_id_with_different_checks_is_accepted() -> None:
    first = "[[query]]\nid='FLK-X'\nquery='(x)'\n"
    second = "[[query]]\nid='FLK-X'\ncheck='float_equality'\nquery='(x)'\n"
    items = parse_catalog(_ENTRY, {"python": first + second}, LANGUAGES)
    assert [q.check for q in items[0].queries] == [None, "float_equality"]


def test_same_id_in_different_languages_is_accepted() -> None:
    query = "[[query]]\nid='FLK-X'\nquery='(x)'\n"
    items = parse_catalog(_ENTRY, {"python": query, "java": query}, LANGUAGES)
    assert [q.language for q in items[0].queries] == ["python", "java"]


def test_only_test_files_are_scanned(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    code = "import time\n\n\ndef f():\n    time.sleep(1)\n"
    (tmp_path / "src" / "a.py").write_text(code, encoding="utf-8")
    (tmp_path / "tests" / "test_a.py").write_text(code, encoding="utf-8")
    (tmp_path / "out").mkdir()
    gates = update_gates(
        tmp_path / "out", tmp_path, ["src/a.py", "tests/test_a.py"], [], CATALOG, renames={}
    )
    assert [v.path for v in gates.violations] == ["tests/test_a.py"]
    item = next(i for i in CATALOG if i.id == "FLK-ASYNC-WAIT")
    assert gates.violations[0].explanation["direction"] == item.explanation.direction


def test_diff_mode_keeps_changed_lines_only() -> None:
    code = "import time\n\n\ndef test_a():\n    time.sleep(1)\n    time.sleep(2)\n"
    assert _detect(code) == [("FLK-ASYNC-WAIT", 5), ("FLK-ASYNC-WAIT", 6)]
    assert _detect(code, ChangedLines({"tests/test_x.py": frozenset({6})})) == [
        ("FLK-ASYNC-WAIT", 6)
    ]


def test_annotation_same_line_and_previous_line() -> None:
    code = (
        "import time\n\n\ndef test_a():\n"
        "    time.sleep(1)  # jigane: allow-flaky FLK-ASYNC-WAIT -- polled by design\n"
        "    # jigane: allow-flaky FLK-ASYNC-WAIT -- rate limit\n"
        "    time.sleep(2)\n"
        "    time.sleep(3)\n"
    )
    assert _detect(code) == [("FLK-ASYNC-WAIT", 8)]


def test_annotation_without_reason_is_violation() -> None:
    code = (
        "import time\n\n\ndef test_a():\n"
        "    time.sleep(1)  # jigane: allow-flaky FLK-ASYNC-WAIT --\n"
    )
    assert ("FLK-ANNOTATION", 5) in _detect(code)
    assert ("FLK-ASYNC-WAIT", 5) in _detect(code)


def test_annotation_unknown_id_is_violation() -> None:
    code = "def test_a():\n    pass  # jigane: allow-flaky FLK-NOPE -- x\n"
    assert _detect(code) == [("FLK-ANNOTATION", 2)]


def test_numbers_are_never_reused(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    test = tmp_path / "tests" / "test_a.py"
    out = tmp_path / "out"
    out.mkdir()
    test.write_text("import time\n\n\ndef test_a():\n    time.sleep(1)\n", encoding="utf-8")
    first = update_gates(out, tmp_path, ["tests/test_a.py"], [], CATALOG, renames={})
    assert first.violations[0].number == "G1"
    test.write_text("def test_a():\n    pass\n", encoding="utf-8")
    assert (
        update_gates(out, tmp_path, ["tests/test_a.py"], [], CATALOG, renames={}).violations == ()
    )
    test.write_text("import time\n\n\ndef test_a():\n    time.sleep(5)\n", encoding="utf-8")
    again = update_gates(out, tmp_path, ["tests/test_a.py"], [], CATALOG, renames={})
    assert again.violations[0].number == "G2"
    test.write_text("import time\n\n\ndef test_a():\n    time.sleep(1)\n", encoding="utf-8")
    back = update_gates(out, tmp_path, ["tests/test_a.py"], [], CATALOG, renames={})
    assert back.violations[0].number == "G1"


def test_reclassified_detection_gets_number_of_new_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_detect(*, undetermined: bool) -> object:
        def _detect_stub(
            _root: object, _paths: object, _catalog: object, _scope: object
        ) -> list[Detection]:
            return [
                QueryDetection(
                    "FLK-ASYNC-WAIT",
                    "tests/test_a.py",
                    5,
                    "time.sleep(1)",
                    undetermined,
                    spec=next(i for i in CATALOG if i.id == "FLK-ASYNC-WAIT").queries[0],
                )
            ]

        return _detect_stub

    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(gates_module, "detect", fake_detect(undetermined=False))
    first = update_gates(out, tmp_path, ["tests/test_a.py"], [], CATALOG, renames={})
    assert [v.number for v in first.violations] == ["G1"]
    monkeypatch.setattr(gates_module, "detect", fake_detect(undetermined=True))
    second = update_gates(out, tmp_path, ["tests/test_a.py"], [], CATALOG, renames={})
    assert second.violations == ()
    assert [w.number for w in second.warnings] == ["W1"]


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str) -> Path:
    root = tmp_path / "shop"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_a.py").write_text(code, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_new_and_refresh_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path, monkeypatch, "import time\n\n\ndef test_a():\n    time.sleep(1)\n")
    assert main(["new", "files", "tests", "--name", "a"]) == EXIT_GATE_VIOLATION
    assert main(["refresh", "local/shop:a"]) == EXIT_GATE_VIOLATION
    (root / "tests" / "test_a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    assert main(["refresh", "local/shop:a"]) == EXIT_OK


def test_warning_only_exits_zero_and_show(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(tmp_path, monkeypatch, "def test_a():\n    f = open('x')\n    f.read()\n")
    assert main(["new", "files", "tests", "--name", "w"]) == EXIT_OK
    capsys.readouterr()
    assert main(["show", "local/shop:w"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "ゲート違反 0 件、警告 1 件" in out
    assert "W1 FLK-RESOURCE-LEAK tests/test_a.py:2" in out
    review = create_feature_review(Path.cwd(), ["tests"], "w2")
    assert review.gates is not None
    assert review.gates.warnings[0].number == "W1"


def test_diff_mode_ignores_unchanged_lines_between_changes(tmp_path: Path) -> None:
    base, head = tmp_path / "base", tmp_path / "head"
    for tree in (base, head):
        (tree / "tests").mkdir(parents=True)
    old = (
        "import time\n\n\ndef test_a():\n    x = 1\n    time.sleep(1)\n"
        "    y = 1\n    return x, y\n"
    )
    new = old.replace("x = 1", "x = 2").replace("y = 1", "y = 2")
    (base / "tests" / "test_a.py").write_text(old, encoding="utf-8")
    (head / "tests" / "test_a.py").write_text(new + "    time.sleep(9)\n", encoding="utf-8")
    segment = Segment(
        "tests/test_a.py",
        "tests/test_a.py",
        Span(5, 7),
        Span(5, 9),
        "",
        Kind.LOGIC,
        LEVEL_OF_KIND[Kind.LOGIC],
        3,
        5,
        None,
        ChangeKind.CHANGED,
    )
    out = tmp_path / "out"
    out.mkdir()
    gates = update_gates(
        out, head, ["tests/test_a.py"], [segment], CATALOG, base_root=base, renames={}
    )
    assert [v.line for v in gates.violations] == [9]


def test_module_global_mutation_is_detected() -> None:
    code = "SHARED = []\n\n\ndef test_a():\n    SHARED.append(1)\n"
    assert ("FLK-ORDER-DEPENDENCY", 5) in _detect(code)


def test_conditional_pytest_skip_in_if_is_detected() -> None:
    code = "import pytest\n\n\ndef test_a():\n    if x:\n        pytest.skip('x')\n"
    assert ("FLK-SKIP-CONDITIONAL", 6) in _detect(code)


def test_symlink_pointing_outside_tree_is_neither_read_nor_reported(tmp_path: Path) -> None:
    secret = tmp_path / "secret" / "test_secret.py"
    secret.parent.mkdir()
    secret.write_text("import time\n\n\ndef test_a():\n    time.sleep(1)\n", encoding="utf-8")
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_link.py").symlink_to(secret)
    (tree / "tests" / "test_dir_link").symlink_to(secret.parent)
    out = tmp_path / "out"
    out.mkdir()
    paths = ["tests/test_link.py", "tests/test_dir_link/test_secret.py"]
    gates = update_gates(out, tree, paths, [], CATALOG, renames={})
    assert gates == Gates()
    assert gates_module._read_lines(tree, "tests/test_link.py") is None
    assert gates_module._read_lines(tree, "tests/test_dir_link/test_secret.py") is None


def test_line_numbers_agree_with_tree_sitter_on_form_feed_and_cr() -> None:
    code = 'x = "a\\x0cb\\rc"\nimport time\n\n\ndef test_a():\n    time.sleep(1)\n'
    assert split_rows(code)[5].strip() == "time.sleep(1)"
    assert _detect(code) == [("FLK-ASYNC-WAIT", 6)]


def test_invalid_utf8_in_comment_does_not_stop_detection() -> None:
    code = (
        b"# \xff\xfe\nimport time\n\n\ndef test_a():\n"
        b"    time.sleep(1)  # jigane: allow-flaky \xff\n"
    )
    found = detect_source("tests/test_x.py", code, ADAPTER, CATALOG, AllLines())
    assert [(d.item_id, d.line) for d in found] == [("FLK-ANNOTATION", 6), ("FLK-ASYNC-WAIT", 6)]


@pytest.mark.parametrize("item", CATALOG, ids=lambda item: item.id)
def test_every_catalog_item_has_the_three_sentences(item: Item) -> None:
    # 説明の無い規則を足すと、返信とレポートの文が空になる。ここで落とす
    explanation = item.explanation
    for sentence in (explanation.concern, explanation.violation, explanation.direction):
        assert sentence.strip().endswith("。")
        assert "FLK-" not in sentence


@pytest.mark.parametrize("missing", ["concern", "violation", "direction"])
def test_catalog_rejects_an_item_without_a_sentence(missing: str) -> None:
    lines = [
        line for line in _ENTRY_HEAD.splitlines(keepends=True) if not line.startswith(missing)
    ]
    with pytest.raises(CatalogError):
        parse_catalog("".join(lines), {}, LANGUAGES)


def test_gate_entries_carry_the_explanation_not_only_the_id(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "out").mkdir()
    code = (
        "import time\n\n\ndef test_a():\n    time.sleep(1)\n"
        "    x = 1  # jigane: allow-flaky FLK-NOPE -- x\n"
    )
    (tmp_path / "tests" / "test_a.py").write_text(code, encoding="utf-8")
    gates = update_gates(tmp_path / "out", tmp_path, ["tests/test_a.py"], [], CATALOG, renames={})
    by_id = {v.id: v for v in gates.violations}
    item = next(i for i in CATALOG if i.id == "FLK-ASYNC-WAIT")
    assert by_id["FLK-ASYNC-WAIT"].explanation == item.explanation.to_json()
    assert by_id[ANNOTATION_ID].explanation == ANNOTATION_EXPLANATION.to_json()


def _old_gates_file(directory: Path, ids: list[str]) -> None:
    entries = [
        {
            "number": f"G{n}",
            "id": item_id,
            "path": "tests/test_a.py",
            "line": 1,
            "excerpt": "x",
            "reason": "",
            "cause": "Annotation",
            "confidence": "direct",
            "origin": "o",
            "legitimate": "",
        }
        for n, item_id in enumerate(ids, 1)
    ]
    state = {"violations": entries, "warnings": []}
    write_gates_file(directory, state)


def test_load_gates_fills_the_explanation_missing_from_an_old_file(tmp_path: Path) -> None:
    item = next(i for i in CATALOG if i.id == "FLK-ASYNC-WAIT")
    _old_gates_file(tmp_path, [item.id, ANNOTATION_ID, "FLK-GONE"])
    loaded = load_gates(tmp_path)
    assert loaded is not None
    by_id = {v.id: v.explanation for v in loaded.violations}
    assert by_id[item.id] == item.explanation.to_json()
    assert by_id[ANNOTATION_ID] == ANNOTATION_EXPLANATION.to_json()
    # 目録に無い ID は注記の文でも別の規則の文でもなく、説明が無いと分かる文になる
    assert by_id["FLK-GONE"] != ANNOTATION_EXPLANATION.to_json()
    assert "FLK-GONE" in by_id["FLK-GONE"]["concern"]
    assert "目録に無い" in by_id["FLK-GONE"]["concern"]


def test_explain_does_not_take_an_unknown_id_for_an_annotation_violation() -> None:
    by_id = {item.id: item for item in CATALOG}
    assert explain("FLK-GONE", by_id) != ANNOTATION_EXPLANATION
    assert explain(ANNOTATION_ID, by_id) == ANNOTATION_EXPLANATION
