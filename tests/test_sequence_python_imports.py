from pathlib import Path

from review_fakes import region_of

from jigane.judgment_map.classification import FilePair, build_judgment_map
from jigane.judgment_map.configuration import Config
from jigane.judgment_map.region_table import Region
from jigane.languages.base import parse
from jigane.languages.python import PythonAdapter
from jigane.sequence.diagram import RegionLabels
from jigane.sequence.index import Index, TypeKey, build_index
from jigane.sequence.resolve import Resolved, extract_calls

adapter = PythonAdapter()


def _table(code: str, path: str) -> dict[str, str]:
    source = code.encode()
    return adapter.imported_names(parse(adapter, source).root_node, source, path)


def _index(tmp_path: Path, files: dict[str, str]) -> Index:
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    return build_index(tmp_path, list(files), adapter)


def _types(index: Index, member_name: str) -> dict[str, tuple[TypeKey, str | None]]:
    member = next(m for m in index.callables.values() if m.name == member_name)
    found: dict[str, tuple[TypeKey, str | None]] = {}
    for call in extract_calls(index, member).calls:
        assert isinstance(call.resolution, Resolved)
        target = call.resolution.member
        found[call.name] = (call.resolution.type, target.qualified_name if target else None)
    return found


def test_imported_names_table() -> None:
    table = _table(
        "import a.b as c\nimport d.e\nfrom x import y, z as w\nfrom . import s\n"
        "from .t import u\nfrom ..v import q\nfrom m import *\n",
        "src/pkg/sub/mod.py",
    )
    assert table == {
        "c": "a.b",
        "d": "d",
        "y": "x.y",
        "w": "x.z",
        "s": "pkg.sub.s",
        "u": "pkg.sub.t.u",
        "q": "pkg.v.q",
    }


def test_relative_import_from_init_uses_the_package_itself() -> None:
    assert _table("from .t import u\n", "src/pkg/__init__.py") == {"u": "pkg.t.u"}


_FILES = {
    "pkg/__init__.py": "",
    "pkg/review.py": "def load_review(a):\n    pass\n",
    "pkg/claude.py": "def run():\n    pass\n",
    "pkg/app.py": (
        "from pkg.review import load_review\n"
        "from . import claude\n"
        "import pkg.claude as c\n"
        "\n"
        "def helper():\n    pass\n"
        "\n"
        "class Service:\n"
        "    def go(self):\n"
        "        load_review(1)\n"
        "        helper()\n"
        "        claude.run()\n"
        "        c.run()\n"
    ),
}


def test_three_call_shapes_resolve(tmp_path: Path) -> None:
    index = _index(tmp_path, _FILES)
    calls = _types(index, "go")
    assert calls["load_review"] == (TypeKey("pkg.review"), "pkg.review.load_review")
    assert calls["helper"] == (TypeKey("pkg.app"), "pkg.app.helper")
    assert calls["run"] == (TypeKey("pkg.claude"), "pkg.claude.run")


def test_local_name_wins_over_import(tmp_path: Path) -> None:
    files = dict(_FILES)
    files["pkg/app.py"] = (
        "from pkg.review import load_review\n\ndef go(load_review):\n    load_review(1)\n"
    )
    index = _index(tmp_path, files)
    assert _types(index, "go")["load_review"][1] is None


def test_region_labels_attach_to_python_declarations(tmp_path: Path) -> None:
    index = _index(tmp_path, _FILES)

    def region(number: int, name: str) -> Region:
        return region_of({"id": f"J{number}", "path": "pkg/app.py", "qualified_name": name})

    labels = RegionLabels(
        index, [region(1, "Service.go"), region(2, "Service"), region(3, "helper")]
    )
    methods = {index.callables[k].name: v for k, v in labels.method.items()}
    assert methods == {"go": "J1", "helper": "J3"}
    assert {index.type_name(k): v for k, v in labels.participant.items()} == {
        "pkg.app.Service": ["J2"]
    }


def _feature_regions(tmp_path: Path, files: dict[str, str]) -> list[Region]:
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    built = build_judgment_map(None, tmp_path, [FilePair(None, p) for p in files], Config())
    return [
        region_of({"id": f"J{number}", "path": r.path, "qualified_name": r.qualified_name})
        for number, r in enumerate(built.regions, start=1)
    ]


def test_feature_review_regions_tag_by_path(tmp_path: Path) -> None:
    regions = _feature_regions(tmp_path, _FILES)
    assert {r.qualified_name for r in regions} == {""}
    index = build_index(tmp_path, list(_FILES), adapter)
    labels = RegionLabels(index, regions)
    by_path = {r.path: r.id for r in regions}
    methods = {index.callables[k].name: v for k, v in labels.method.items()}
    assert methods["go"] == by_path["pkg/app.py"]
    assert methods["load_review"] == by_path["pkg/review.py"]
    participants = {index.type_name(k): v for k, v in labels.participant.items()}
    assert participants["pkg.app.Service"] == [by_path["pkg/app.py"]]


def test_named_region_wins_over_file_region(tmp_path: Path) -> None:
    index = _index(tmp_path, _FILES)
    file_region = region_of({"id": "J1", "path": "pkg/app.py", "qualified_name": ""})
    named = region_of({"id": "J2", "path": "pkg/app.py", "qualified_name": "Service.go"})
    labels = RegionLabels(index, [file_region, named])
    methods = {index.callables[k].name: v for k, v in labels.method.items()}
    assert methods["go"] == "J2"
    assert methods["helper"] == "J1"


def test_file_region_of_missing_path_tags_nothing(tmp_path: Path) -> None:
    index = _index(tmp_path, _FILES)
    labels = RegionLabels(
        index, [region_of({"id": "J1", "path": "pkg/gone.py", "qualified_name": ""})]
    )
    assert labels.method == {}
    assert labels.participant == {}


def test_named_region_of_an_unknown_declaration_tags_nothing(tmp_path: Path) -> None:
    index = _index(tmp_path, _FILES)
    labels = RegionLabels(
        index,
        [
            region_of({"id": "J1", "path": "pkg/app.py", "qualified_name": "Ghost"}),
            region_of({"id": "J2", "path": "pkg/app.py", "qualified_name": "Ghost.run"}),
        ],
    )
    assert labels.method == {}
    assert labels.participant == {}
