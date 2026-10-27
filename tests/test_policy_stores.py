from pathlib import Path

from test_policy_classes import feature_review

from jigane.figures.models import TableFigure
from jigane.skills import macro, policy_stores
from jigane.skills.assembly import AssemblyInput
from jigane.skills.policy_figures import REGISTRY, diagrams_of

SOURCE = """import csv
import json
from dataclasses import asdict, dataclass

TABLE_FILE = "regions.csv"


def save(path, rows):
    with open(path / TABLE_FILE, "w") as handle:
        writer = csv.DictWriter(handle, fieldnames=["region", "start", "end"])
        writer.writerows(rows)


def load(path):
    with open(path / TABLE_FILE) as handle:
        return [(row["region"], row["prefix"]) for row in csv.DictReader(handle)]


@dataclass
class Plan:
    name: str
    count: int

    def to_json(self):
        return asdict(self)

    @classmethod
    def from_json(cls, raw):
        return cls(name=raw["name"], count=raw["count"], extra=raw.get("extra"))
"""


def test_mismatched_keys_show_in_the_difference_columns(tmp_path: Path) -> None:
    figure = policy_stores.store_table(feature_review(tmp_path, "s.py", SOURCE))
    assert isinstance(figure, TableFigure)
    rows = {row.cells[0]: row.cells for row in figure.rows}
    csv_row = rows["regions.csv"]
    assert csv_row[1] == "save: end, region, start"
    assert csv_row[2] == "load: prefix, region"
    assert csv_row[3] == "end, start"
    assert csv_row[4] == "prefix"
    plan = rows["Plan（to_json と from_json）"]
    assert plan[2] == "Plan.from_json: count, extra, name（作る型: Plan）"
    assert plan[3] == ""
    assert plan[4] == "extra"


def test_no_figure_when_the_change_touches_no_store(tmp_path: Path) -> None:
    (tmp_path / "store.py").write_text(SOURCE, encoding="utf-8")
    review = feature_review(tmp_path, "m.py", "x = 1\n")
    assert policy_stores.store_table(review) is None
    assert "stores" not in [p.key for p in diagrams_of(review).figures]


def test_registered_and_reaches_prompt(tmp_path: Path) -> None:
    assert any(e.key == "stores" for e in REGISTRY)
    review = feature_review(tmp_path, "s.py", SOURCE)
    text = macro.diagram_materials_text(AssemblyInput.load(review), macro.policy_sections(review))
    assert "### 保存物の書く側と読む側" in text


def write_project(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")


TABLE_MODULE = """from enum import StrEnum


class Column(StrEnum):
    REGION = "region"
    NUMBER = "no"
    START = "start"


class Other(StrEnum):
    NUMBER = "no"


REQUIRED_COLUMNS = (Column.NUMBER, Column.START)


def table_columns(with_region: bool) -> tuple[Column, ...]:
    columns = [Column.NUMBER, Column.START]
    if with_region:
        columns.insert(0, Column.REGION)
    return tuple(columns)
"""
DRAFT_MODULE = """import csv

from table import table_columns


def write_csv(path, header, rows):
    with open(path, "w") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def run(arguments):
    header = table_columns(arguments.region is not None)
    write_csv(arguments.output, header, [])
"""
RENDER_MODULE = """import csv

from table import REQUIRED_COLUMNS, Column


def read_intervals(table_path):
    with open(table_path) as handle:
        reader = csv.DictReader(handle)
        missing = [n for n in REQUIRED_COLUMNS if n not in (reader.fieldnames or [])]
        return [(row[Column.NUMBER], row[Column.START]) for row in reader]
"""


def test_enum_vocabulary_binds_a_csv_whose_path_is_an_argument(tmp_path: Path) -> None:
    write_project(tmp_path, {"table.py": TABLE_MODULE, "render.py": RENDER_MODULE})
    figure = policy_stores.store_table(feature_review(tmp_path, "draft.py", DRAFT_MODULE))
    assert isinstance(figure, TableFigure)
    rows = {row.cells[0]: row.cells for row in figure.rows}
    cells = rows["Column の列を持つ CSV"]
    assert cells[1] == "run: no, region, start"
    assert cells[2] == "read_intervals: no, start"
    assert cells[3] == "region"
    assert cells[4] == ""


def test_a_different_vocabulary_does_not_bind_to_the_pair(tmp_path: Path) -> None:
    other = RENDER_MODULE.replace("Column", "Other")
    write_project(tmp_path, {"table.py": TABLE_MODULE, "render.py": other})
    figure = policy_stores.store_table(feature_review(tmp_path, "draft.py", DRAFT_MODULE))
    assert isinstance(figure, TableFigure)
    rows = {row.cells[0]: row.cells for row in figure.rows}
    assert rows["Column の列を持つ CSV"][2] == "（見つからない）"


def test_plain_string_keys_without_a_name_are_not_bound(tmp_path: Path) -> None:
    source = (
        "import csv\n\n\ndef save(path, rows):\n"
        "    with open(path, 'w') as handle:\n"
        "        csv.writer(handle).writerow(['a', 'b'])\n"
    )
    assert policy_stores.store_table(feature_review(tmp_path, "s.py", source)) is None
