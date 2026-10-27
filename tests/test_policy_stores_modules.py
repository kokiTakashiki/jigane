from pathlib import Path

from review_fakes import make_review
from test_policy_classes import whole

from jigane.figures.models import TableFigure
from jigane.languages import python_stores
from jigane.reviews.lifecycle import Review
from jigane.skills import policy_stores
from jigane.skills.policy_figures import FigureSlot, section_texts
from jigane.storage.store import ReviewKind


def project_review(root: Path, files: dict[str, str]) -> Review:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    names = sorted(files)
    segments = [whole(n, len(files[n].splitlines())) for n in names]
    return make_review(
        root,
        names,
        judgment_map={"segments": segments, "regions": [], "line_counts": {}},
        kind=ReviewKind.FEATURE,
    )


def store_rows(review: Review) -> dict[str, tuple[str, ...]]:
    figure = policy_stores.store_table(review)
    assert isinstance(figure, TableFigure)
    return {row.cells[0]: row.cells for row in figure.rows}


# 同名の定数 TABLE を、import の三つの書き方で取り込む
SAME_NAME_CONSTANTS = {
    "a/names.py": 'TABLE = "a.json"\n',
    "a/save.py": (
        "import json\nfrom names import TABLE\n\n\n"
        "def save(path):\n    json.dump({'a_key': 1}, open(path / TABLE, 'w'))\n\n\n"
        "def load(path):\n    return json.load(open(path / TABLE))['a_key']\n"
    ),
    "b/names.py": 'TABLE = "b.json"\n',
    "b/save.py": (
        "import json\nfrom .names import TABLE\n\n\n"
        "def save(path):\n    json.dump({'b_key': 1}, open(path / TABLE, 'w'))\n\n\n"
        "def load(path):\n    return json.load(open(path / TABLE))['b_key']\n"
    ),
    "c/names.py": 'TABLE = "c.json"\n',
    "c/save.py": (
        "import json\nimport names\n\n\n"
        "def save(path):\n    json.dump({'c_key': 1}, open(path / names.TABLE, 'w'))\n\n\n"
        "def load(path):\n    return json.load(open(path / names.TABLE))['c_key']\n"
    ),
}


def test_same_named_constants_resolve_through_each_import_form(tmp_path: Path) -> None:
    rows = store_rows(project_review(tmp_path, SAME_NAME_CONSTANTS))
    for letter in "abc":
        row = rows[f"{letter}.json"]
        assert row[1] == f"save: {letter}_key"
        assert row[2] == f"load: {letter}_key"
        assert row[3] == ""
        assert row[4] == ""


def table_module(value: str) -> str:
    return f'from enum import StrEnum\n\n\nclass Column(StrEnum):\n    REGION = "{value}"\n'


HELPER = "from table import Column\n\n\ndef header():\n    return [Column.REGION]\n"
WRITER = (
    "import csv\nfrom helper import header\n\n\n"
    "def write(handle):\n    csv.writer(handle).writerow(header())\n"
)
READER = (
    "import csv\nfrom table import Column\n\n\n"
    "def read(path):\n    return [row[Column.REGION] for row in csv.DictReader(open(path))]\n"
)


def test_same_named_enums_and_functions_resolve_to_their_own_module(tmp_path: Path) -> None:
    files = {}
    for letter in "ab":
        files[f"{letter}/table.py"] = table_module(f"{letter}_region")
        files[f"{letter}/helper.py"] = HELPER
        files[f"{letter}/write.py"] = WRITER
        files[f"{letter}/read.py"] = READER
    rows = store_rows(project_review(tmp_path, files))
    for letter in "ab":
        row = rows[f"Column（{letter}/table.py） の列を持つ CSV"]
        assert row[1] == f"write: {letter}_region"
        assert row[2] == f"read: {letter}_region"
        assert row[3] == ""
        assert row[4] == ""


def test_same_named_sinks_resolve_to_their_own_module(tmp_path: Path) -> None:
    # a の emit は JSON を書く受け口、b の emit は何も書かない。b の呼び出しが a の受け口に束ねない
    files = {
        "a/emit.py": "import json\n\n\ndef emit(rows):\n    json.dump(rows, open('x', 'w'))\n",
        "b/emit.py": "def emit(rows):\n    return rows\n",
        "b/run.py": 'from emit import emit\n\n\ndef run():\n    emit({"b_key": 1}, "b.json")\n',
    }
    assert policy_stores.store_table(project_review(tmp_path, files)) is None


def test_ambiguous_name_is_not_bound_by_guessing(tmp_path: Path) -> None:
    files = {
        "a/names.py": 'TABLE = "a.json"\n',
        "b/names.py": 'TABLE = "b.json"\n',
        "c/save.py": (
            "import json\n\n\ndef save(path):\n    json.dump({'k': 1}, open(path / TABLE, 'w'))\n"
        ),
    }
    assert policy_stores.store_table(project_review(tmp_path, files)) is None


def test_unique_name_is_bound_when_no_import_leads_to_it(tmp_path: Path) -> None:
    files = {
        "names.py": 'TABLE = "t.json"\n',
        "save.py": (
            "import json\n\n\ndef save(path):\n    json.dump({'k': 1}, open(path / TABLE, 'w'))\n"
        ),
    }
    assert store_rows(project_review(tmp_path, files))["t.json"][1] == "save: k"


def test_unsupported_language_note_appears_with_the_table(tmp_path: Path) -> None:
    files = {
        "s.py": 'import json\n\n\ndef save(path):\n    json.dump({"k": 1}, open("t.json", "w"))\n',
        "A.swift": "struct A {}\n",
    }
    texts = section_texts(project_review(tmp_path, files), FigureSlot.AFTER_TREE)
    store = next(t for t in texts if t.startswith("### 保存物の書く側と読む側"))
    assert "t.json" in store
    assert (
        "対応外の言語: Swift（jigane の Swift の解析は、この表の読み取りにまだ対応していない）。"
        in store
    )


# リポジトリ内に外部ライブラリと同名のファイル（tools/json.py、tools/csv.py）がある
SHADOWING_FILES = {
    "tools/json.py": (
        "import csv\n\n\ndef dump(value, handle):\n    csv.writer(handle).writerow(value)\n"
    ),
    "tools/csv.py": "class DictReader:\n    pass\n",
}


def test_external_import_is_not_bound_to_a_same_named_file(tmp_path: Path) -> None:
    files = {
        **SHADOWING_FILES,
        "app/save.py": (
            "import json\nfrom csv import DictReader\n\n\n"
            "def save(path):\n    json.dump({'k': 1}, open('t.json', 'w'))\n\n\n"
            "def load(path):\n    return json.load(open('t.json'))['k']\n"
        ),
    }
    rows = store_rows(project_review(tmp_path, files))
    assert rows["t.json"][1] == "save: k"
    assert rows["t.json"][2] == "load: k"


def test_fully_qualified_import_binds_to_the_file(tmp_path: Path) -> None:
    # 末尾が同じ names.py が二つあっても、完全修飾名 tools.names は tools/names.py だけに当たる
    files = {
        "tools/names.py": 'TABLE = "t.json"\n',
        "other/names.py": 'TABLE = "wrong.json"\n',
        "app/save.py": (
            "import json\nfrom tools.names import TABLE\n\n\n"
            "def save(path):\n    json.dump({'k': 1}, open(path / TABLE, 'w'))\n"
        ),
    }
    assert store_rows(project_review(tmp_path, files))["t.json"][1] == "save: k"


def test_package_root_import_binds_through_the_package(tmp_path: Path) -> None:
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/names.py": 'TABLE = "t.json"\n',
        "other/names.py": 'TABLE = "wrong.json"\n',
        "app/save.py": (
            "import json\nfrom pkg.names import TABLE\n\n\n"
            "def save(path):\n    json.dump({'k': 1}, open(path / TABLE, 'w'))\n"
        ),
    }
    assert store_rows(project_review(tmp_path, files))["t.json"][1] == "save: k"


def test_absolute_import_resolves_by_fully_qualified_name_only() -> None:
    modules = python_stores._Modules(
        {
            "tools/json.py",
            "tools/csv.py",
            "app/save.py",
            "src/pkg/__init__.py",
            "src/pkg/names.py",
            "other/names.py",
        }
    )
    # 外部ライブラリと同名のファイルがあっても、末尾の一致では束ねない
    assert modules.absolute("app/save.py", "json") is None
    assert modules.absolute("app/save.py", "csv") is None
    # 完全修飾名には当たる
    assert modules.absolute("app/save.py", "tools.json") == "tools/json.py"
    assert modules.absolute("app/save.py", "pkg.names") == "src/pkg/names.py"
    # 同じ階層のファイルを名前だけで読むスクリプト式の import は、先に当てる
    assert modules.absolute("tools/run.py", "json") == "tools/json.py"


def test_imported_name_that_cannot_be_traced_is_not_bound_by_uniqueness(tmp_path: Path) -> None:
    files = {
        "names.py": 'TABLE = "t.json"\n',
        "save.py": (
            "import json\nfrom external import TABLE\n\n\n"
            "def save(path):\n    json.dump({'k': 1}, open(path / TABLE, 'w'))\n"
        ),
    }
    assert policy_stores.store_table(project_review(tmp_path, files)) is None


def test_read_side_counts_only_types_defined_in_the_repository(tmp_path: Path) -> None:
    files = {
        "models.py": (
            "from dataclasses import dataclass\n\n\n@dataclass\nclass DayNight:\n    a: int\n\n\n"
            "class Interval:\n    def __init__(self, a):\n        self.a = a\n"
        ),
        "load.py": (
            "import json\nfrom models import DayNight, Interval\n\n\n"
            "def save(path):\n    json.dump({'a': 1}, open('t.json', 'w'))\n\n\n"
            "def load(path):\n    raw = json.load(open('t.json'))\n"
            "    ValueError(raw['a'])\n    DayNight(raw['a'])\n    return Interval(raw['a'])\n"
        ),
    }
    row = store_rows(project_review(tmp_path, files))["t.json"]
    assert "作る型: DayNight, Interval）" in row[2]
    assert "ValueError" not in row[2]


def access_keys(sources: dict[str, str]) -> dict[str, tuple[str, str, list[str]]]:
    return {
        side.function: (side.role.value, side.kind.value, sorted(side.keys))
        for access in python_stores.store_accesses(sources)
        for side in access.sides
    }


def test_each_import_form_binds_the_module_it_names() -> None:
    sources = {
        "pkg/__init__.py": "",
        "pkg/names.py": 'TABLE = "t.json"\n',
        "star.py": (
            "from pkg.names import *\nimport json\n\n\n"
            "def save_star(p):\n    json.dump({'s': 1}, open(p / 'u.json', 'w'))\n"
        ),
        "dotted.py": (
            "import pkg.names\nimport json\n\n\n"
            "def save_dotted(p):\n    json.dump({'d': 1}, open(p / pkg.names.TABLE, 'w'))\n"
        ),
        "sub.py": (
            "from pkg import names\nimport json\n\n\n"
            "def save_sub(p):\n    json.dump({'m': 1}, open(p / names.TABLE, 'w'))\n"
        ),
        "alias.py": (
            "import pkg.names as n\nimport json\n\n\n"
            "def save_alias(p):\n    json.dump({'a': 1}, open(p / n.TABLE, 'w'))\n"
        ),
        "rel/__init__.py": "",
        "rel/names.py": 'TABLE = "r.json"\n',
        "rel/save.py": (
            "from . import names\nfrom .names import TABLE\nimport json\n\n\n"
            "def save_rel(p):\n    json.dump({'r': 1}, open(p / TABLE, 'w'))\n"
        ),
    }
    accesses = {a.path: a.names for a in python_stores.store_accesses(sources)}
    assert accesses == {
        "star.py": frozenset({"u.json"}),
        "dotted.py": frozenset(),
        "sub.py": frozenset({"t.json"}),
        "alias.py": frozenset({"t.json"}),
        "rel/save.py": frozenset({"r.json"}),
    }


KEY_CONSTANTS = "".join(f'{name} = "{name.lower()}"\n' for name in "ABCDEFG") + (
    "SHARED = [A, B]\n"
)

EXPR_KEYS_WRITER = """import csv
import json
from k import A, B, C, D, E, F, G, SHARED

COLS = [A, B]


def deep(x):
    return deep2(x)


def deep2(x):
    return deep3(x)


def deep3(x):
    return deep4(x)


def deep4(x):
    return deep5(x)


def deep5(x):
    return deep6(x)


def deep6(x):
    return deep7(x)


def deep7(x):
    return deep8(x)


def deep8(x):
    return [A]


def w_list(h):
    csv.DictWriter(h, fieldnames=[A, B])


def w_rows_direct(h):
    csv.writer(h).writerows([A, B])


def w_rows_nested(h):
    csv.writer(h).writerows([[A, B]])


def w_rows_name(h, rows):
    csv.writer(h).writerows(rows)


def w_unlisted_node(h, table):
    csv.writer(h).writerow(table[0])


def w_plus(h):
    csv.writer(h).writerow([A] + [B, *[C]])


def w_ifexp(h, f):
    csv.writer(h).writerow([D] if f else [E])


def w_same_file(h):
    csv.writer(h).writerow(COLS)


def w_other_file(h):
    csv.writer(h).writerow(SHARED)


def w_too_deep(h):
    csv.writer(h).writerow(deep(1))


def w_dict(h):
    csv.writer(h).writerow({F: 1})


def w_conversion(h):
    cols = [G]
    cols.append(A)
    csv.writer(h).writerow(tuple(cols))


def w_subscript(d):
    d[A] = 1
    json.dump(d, open('x.json', 'w'))
"""


def test_expression_keys_follow_each_node_kind() -> None:
    keys = access_keys({"k.py": KEY_CONSTANTS, "w.py": EXPR_KEYS_WRITER})
    # w_rows_name と w_unlisted_node は鍵を辿れる式の形でないので、一覧に載らない
    csv_kind = ("write", "CSV")
    assert keys == {
        "w_list": (*csv_kind, ["a", "b"]),
        "w_rows_direct": (*csv_kind, ["a", "b"]),
        "w_plus": (*csv_kind, ["a", "b", "c"]),
        "w_ifexp": (*csv_kind, ["d", "e"]),
        "w_same_file": (*csv_kind, ["a", "b"]),
        "w_other_file": (*csv_kind, ["a", "b"]),
        "w_dict": (*csv_kind, ["f"]),
        "w_conversion": (*csv_kind, ["a", "g"]),
        "w_subscript": ("write", "JSON", ["a"]),
    }


def test_write_side_keys_cover_dict_asdict_and_sink_arguments() -> None:
    sources = {
        "w.py": (
            "import json\nfrom dataclasses import asdict, dataclass\n\n\n"
            "def emit(rows):\n    json.dump(rows, open('x.json', 'w'))\n\n\n"
            "def run():\n    emit({'via_sink': 1})\n    emit(rows={'via_keyword': 1})\n\n\n"
            "def recurse(n):\n    emit({'k': 1})\n    recurse(n - 1)\n\n\n"
            "@dataclass\nclass Row:\n    a: int\n    b: int\n\n"
            "    def to_json(self):\n        return asdict(self)\n\n\n"
            "def literal():\n    return {'plain': 1}\n"
        ),
    }
    keys = access_keys(sources)
    assert keys["run"] == ("write", "JSON", ["via_keyword", "via_sink"])
    assert keys["Row.to_json"] == ("write", "JSON", ["a", "b"])
    assert "literal" not in keys


def test_read_side_binds_names_through_with_and_assignment_chains() -> None:
    sources = {
        "k.py": 'A = "a"\n',
        "r.py": (
            "import json\nfrom k import A\n\n\n"
            "def r_with(p):\n"
            "    with open(p) as fh, json.load(fh) as obj:\n        pass\n"
            "    with open(p) as fh2:\n        data = json.load(fh2)\n"
            "    for row in data:\n        x = row[A]\n    return x\n\n\n"
            "def r_chain_within(p):\n"
            "    g = f\n    f = e\n    e = json.load(open(p))\n"
            "    return [i[A] for i in g]\n\n\n"
            "def r_chain_beyond(p):\n"
            "    h = g\n    g = f\n    f = e\n    e = d\n    d = json.load(open(p))\n"
            "    return [i[A] for i in h]\n\n\n"
            "def r_unrelated(p):\n    return json.load(open(p))\n"
        ),
    }
    keys = access_keys(sources)
    assert keys == {
        "r_with": ("read", "JSON", ["a"]),
        "r_chain_within": ("read", "JSON", ["a"]),
        # 代入の連鎖を辿る走査は四巡で止まる。逆順の長い連鎖は辿り切れない
    }


def test_store_accesses_skips_sources_ast_cannot_parse() -> None:
    deep = "x = " + "+".join(["1"] * 100000) + "\n"
    nested = "x = " + "(" * 3000 + "1" + ")" * 3000 + "\n"
    sources = {"deep.py": deep, "nested.py": nested, "nul.py": "x = 1\0\n"}
    assert python_stores.store_accesses(sources) == []
