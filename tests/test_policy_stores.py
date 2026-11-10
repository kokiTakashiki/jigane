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


SWIFT_SOURCE = """
struct Settings {
    func save() {
        UserDefaults.standard.set("x", forKey: "written")
        UserDefaults.standard.set("x", forKey: "both")
    }
    func load() -> String? {
        _ = UserDefaults.standard.string(forKey: "read")
        return UserDefaults.standard.string(forKey: "both")
    }
}
"""


def test_swift_user_defaults_row_shows_write_only_and_read_only_keys(tmp_path: Path) -> None:
    figure = policy_stores.store_table(feature_review(tmp_path, "Settings.swift", SWIFT_SOURCE))
    assert isinstance(figure, TableFigure)
    row = {r.cells[0]: r.cells for r in figure.rows}["UserDefaults"]
    assert row[1] == "Settings.save: both, written"
    assert row[2] == "Settings.load: both, read"
    assert row[3] == "written"
    assert row[4] == "read"


SWIFT_CODABLE_SOURCE = """
struct Profile: Codable {
    var name: String
    var age: Int
}
struct Repo {
    func save(_ profile: Profile) throws {
        _ = try JSONEncoder().encode(profile)
    }
}
struct Reader {
    func load(data: Data) throws -> Profile {
        try JSONDecoder().decode(Profile.self, from: data)
    }
}
"""


def test_swift_codable_json_row_binds_by_type_name(tmp_path: Path) -> None:
    review = feature_review(tmp_path, "Profile.swift", SWIFT_CODABLE_SOURCE)
    figure = policy_stores.store_table(review)
    assert isinstance(figure, TableFigure)
    row = {r.cells[0]: r.cells for r in figure.rows}["Profile の JSON"]
    assert row[1] == "Repo.save: age, name"
    assert row[2] == "Reader.load: age, name（作る型: Profile）"
    assert row[3] == ""
    assert row[4] == ""


def test_swift_codable_json_without_reader_shows_unknown_reader(tmp_path: Path) -> None:
    source = SWIFT_CODABLE_SOURCE.replace("decode(Profile.self", "decode(Other.self")
    review = feature_review(tmp_path, "Profile.swift", source)
    figure = policy_stores.store_table(review)
    assert isinstance(figure, TableFigure)
    rows = {r.cells[0]: r.cells for r in figure.rows}
    assert rows["Profile の JSON"][2] == policy_stores.NOT_FOUND
    assert rows["Other の JSON"][1] == policy_stores.NOT_FOUND


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


def test_plain_string_keys_without_a_name_stay_in_their_own_parameter_store(
    tmp_path: Path,
) -> None:
    source = (
        "import csv\n\n\ndef save(path, rows):\n"
        "    with open(path, 'w') as handle:\n"
        "        csv.writer(handle).writerow(['a', 'b'])\n"
    )
    figure = policy_stores.store_table(feature_review(tmp_path, "s.py", source))
    assert isinstance(figure, TableFigure)
    assert [row.cells[0] for row in figure.rows] == ["save の引数 path"]


HANDWRITTEN = """BOOKMARKS_FILE = "bookmarks.txt"


def save_bookmark(url, title):
    with open(BOOKMARKS_FILE, "a") as f:
        f.write(url + "|" + title + "\\n")


def load_bookmarks():
    with open(BOOKMARKS_FILE) as f:
        return [line.strip().split("|") for line in f]
"""

HANDWRITTEN_WRITE_ONLY = """def save_bookmark(url, title):
    with open("bookmarks.txt", "a") as f:
        f.write(f"{url}|{title}\\n")
"""

PARAMETER_PATH = """import json


def save(path, url):
    with open(path, "w") as f:
        json.dump({"url": url}, f)
"""


def test_custom_text_store_lists_writer_reader_and_format(tmp_path: Path) -> None:
    figure = policy_stores.store_table(feature_review(tmp_path, "h.py", HANDWRITTEN))
    assert isinstance(figure, TableFigure)
    row = {row.cells[0]: row.cells for row in figure.rows}["bookmarks.txt"]
    assert row[1] == "save_bookmark"
    assert row[2] == "load_bookmarks"
    assert row[5] == "独自の形式"


def test_custom_store_without_a_found_reader_is_not_called_absent(tmp_path: Path) -> None:
    figure = policy_stores.store_table(feature_review(tmp_path, "h.py", HANDWRITTEN_WRITE_ONLY))
    assert isinstance(figure, TableFigure)
    row = {row.cells[0]: row.cells for row in figure.rows}["bookmarks.txt"]
    assert row[1] == "save_bookmark"
    assert row[2] == policy_stores.UNIDENTIFIED
    assert policy_stores.NOT_FOUND not in row


def test_file_passed_as_a_parameter_is_a_store_named_after_the_function(tmp_path: Path) -> None:
    figure = policy_stores.store_table(feature_review(tmp_path, "p.py", PARAMETER_PATH))
    assert isinstance(figure, TableFigure)
    row = {row.cells[0]: row.cells for row in figure.rows}["save の引数 path"]
    assert row[1] == "save: url"
    assert row[5] == "JSON"


SAVE_MODULE = """import json


def save(path, url):
    with open(path, "w") as f:
        json.dump({"url": url}, f)
"""
LOAD_MODULE = """import csv


def load(path):
    with open(path) as f:
        return [row["url"] for row in csv.DictReader(f)]
"""
FIXED_NAME_WRITE_ONLY = """import json


def save(url):
    with open("urls.json", "w") as f:
        json.dump({"url": url}, f)
"""


def test_stores_passed_by_parameter_say_unidentified_for_the_other_side(tmp_path: Path) -> None:
    write_project(tmp_path, {"load_urls.py": LOAD_MODULE})
    figure = policy_stores.store_table(feature_review(tmp_path, "save_urls.py", SAVE_MODULE))
    assert isinstance(figure, TableFigure)
    rows = {row.cells[0]: row.cells for row in figure.rows}
    assert rows["save の引数 path"][2] == policy_stores.UNIDENTIFIED
    assert policy_stores.NOT_FOUND not in rows["save の引数 path"]


def test_reading_side_passed_by_parameter_says_unidentified_for_the_writer(tmp_path: Path) -> None:
    write_project(tmp_path, {"save_urls.py": SAVE_MODULE})
    figure = policy_stores.store_table(feature_review(tmp_path, "load_urls.py", LOAD_MODULE))
    assert isinstance(figure, TableFigure)
    rows = {row.cells[0]: row.cells for row in figure.rows}
    assert rows["load の引数 path"][1] == policy_stores.UNIDENTIFIED
    assert policy_stores.NOT_FOUND not in rows["load の引数 path"]


def test_fixed_name_store_without_a_counterpart_stays_not_found(tmp_path: Path) -> None:
    figure = policy_stores.store_table(feature_review(tmp_path, "s.py", FIXED_NAME_WRITE_ONLY))
    assert isinstance(figure, TableFigure)
    row = {row.cells[0]: row.cells for row in figure.rows}["urls.json"]
    assert row[2] == policy_stores.NOT_FOUND


SWIFT_KEYCHAIN_SOURCE = """
struct Vault {
    func save() {
        let q: [String: Any] = [kSecAttrAccount as String: "written", kSecAttrService: "s"]
        SecItemAdd(q as CFDictionary, nil)
        SecItemAdd([kSecAttrAccount as String: "both"] as CFDictionary, nil)
    }
    func load() {
        SecItemCopyMatching([kSecAttrAccount as String: "read"] as CFDictionary, &r)
        SecItemCopyMatching([kSecAttrAccount as String: "both"] as CFDictionary, &r)
    }
}
"""


def test_swift_keychain_row_shows_write_only_and_read_only_accounts(tmp_path: Path) -> None:
    figure = policy_stores.store_table(
        feature_review(tmp_path, "Vault.swift", SWIFT_KEYCHAIN_SOURCE)
    )
    assert isinstance(figure, TableFigure)
    row = {r.cells[0]: r.cells for r in figure.rows}["Keychain"]
    assert row[1] == "Vault.save: both, written"
    assert row[2] == "Vault.load: both, read"
    assert row[3] == "written"
    assert row[4] == "read"
