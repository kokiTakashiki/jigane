import re
import tomllib
from functools import cache
from pathlib import Path
from typing import Any

import grimp

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "jigane"
README = PACKAGE / "README.md"
PYPROJECT = ROOT / "pyproject.toml"
SUBGRAPH = re.compile(r"^subgraph\s+(\w+)$")
NAME = re.compile(r"^\w+$")
# README の層の名前を、契約の層の並び（上から cli、feature、core）の位置へ対応づける。
CONTRACT_ORDER = ["cli", "feature", "core"]


def subpackages() -> set[str]:
    return {init.parent.name for init in PACKAGE.glob("*/__init__.py")}


def diagram_layers() -> dict[str, set[str]]:
    """README の mermaid の subgraph から、層の名前と中のサブパッケージを読む。"""
    text = README.read_text(encoding="utf-8")
    layers: dict[str, set[str]] = {}
    for block in re.findall(r"```mermaid\n(.*?)```", text, flags=re.DOTALL):
        current: set[str] | None = None
        for line in block.splitlines():
            stripped = line.strip()
            opened = SUBGRAPH.match(stripped)
            if opened is not None:
                current = layers.setdefault(opened.group(1), set())
            elif stripped == "end":
                current = None
            elif current is not None and NAME.match(stripped):
                current.add(stripped)
            else:
                # subgraph の外の行（図の見出しや層どうしの矢印）は、層の割り当てを持たない
                continue
    return layers


def contract_layers() -> dict[str, set[str]]:
    """[tool.importlinter] の layers 契約から、層ごとのサブパッケージを読む。

    契約の並びは上の層から順で、README の層の名前を CONTRACT_ORDER で対応づける。
    """
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    contracts = config["tool"]["importlinter"]["contracts"]
    layered = [contract for contract in contracts if contract["type"] == "layers"]
    assert len(layered) == 1, "layers 契約が一つではない"
    lines = layered[0]["layers"]
    assert len(lines) == len(CONTRACT_ORDER), f"契約の層の数が {len(CONTRACT_ORDER)} ではない"
    return {
        name: {member.strip() for member in re.split(r"[:|]", line)}
        for name, line in zip(CONTRACT_ORDER, lines, strict=True)
    }


def test_every_subpackage_is_in_exactly_one_readme_layer() -> None:
    layers = diagram_layers()
    packages = subpackages()
    assert layers != {}, "README に層の subgraph が無い"
    assert packages != set(), "サブパッケージが見つからない"
    counts: dict[str, int] = {}
    for members in layers.values():
        for name in members:
            counts[name] = counts.get(name, 0) + 1
    missing = sorted(packages - counts.keys())
    unknown = sorted(counts.keys() - packages)
    repeated = sorted(name for name, count in counts.items() if count > 1)
    assert (missing, unknown, repeated) == ([], [], []), (
        f"どの層にも無いサブパッケージ: {missing}\n"
        f"サブパッケージでない名前: {unknown}\n"
        f"二つ以上の層にある名前: {repeated}"
    )


def test_readme_layers_match_import_linter_contract() -> None:
    diagram = diagram_layers()
    contract = contract_layers()
    assert set(diagram) == set(CONTRACT_ORDER), f"README の層の名前が {CONTRACT_ORDER} と違う"
    differences = {
        name: {
            "図にだけある": sorted(diagram[name] - contract[name]),
            "契約にだけある": sorted(contract[name] - diagram[name]),
        }
        for name in CONTRACT_ORDER
        if diagram[name] != contract[name]
    }
    assert differences == {}, (
        f"README の図と [tool.importlinter] の層の割り当てが違う: {differences}"
    )


def test_import_linter_contract_covers_every_subpackage() -> None:
    contract = contract_layers()
    listed = set().union(*contract.values())
    packages = subpackages()
    assert (sorted(packages - listed), sorted(listed - packages)) == ([], []), (
        "契約に無いサブパッケージ、またはサブパッケージでない名前がある"
    )


@cache
def import_graph() -> grimp.ImportGraph:
    # 関数の中の import と TYPE_CHECKING の中の import も辺に入る。lint-imports の数え方と同じ。
    return grimp.build_graph("jigane")


def sibling_pairs() -> list[tuple[str, str]]:
    """同じ層の兄弟の、順序つきの組。層に一つしかないサブパッケージは組を持たない。"""
    pairs: list[tuple[str, str]] = []
    for members in contract_layers().values():
        pairs.extend((a, b) for a in sorted(members) for b in sorted(members) if a != b)
    return pairs


def sibling_contracts() -> dict[str, list[dict[str, Any]]]:
    """source_modules がサブパッケージ一つだけの forbidden 契約を、サブパッケージごとに集める。"""
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    found: dict[str, list[dict[str, Any]]] = {}
    for contract in config["tool"]["importlinter"]["contracts"]:
        sources = contract.get("source_modules", [])
        if contract["type"] == "forbidden" and len(sources) == 1 and sources[0].count(".") == 1:
            found.setdefault(sources[0].removeprefix("jigane."), []).append(contract)
        else:
            # layers 契約と、サブパッケージの一部のモジュールだけを守る契約は、兄弟の宣言ではない
            continue
    return found


def directly_imports(importer: str, imported: str) -> bool:
    graph = import_graph()
    targets = {f"jigane.{imported}"} | graph.find_descendants(f"jigane.{imported}")
    sources = {f"jigane.{importer}"} | graph.find_descendants(f"jigane.{importer}")
    return any(graph.find_modules_directly_imported_by(module) & targets for module in sources)


def test_every_subpackage_with_siblings_has_exactly_one_sibling_contract() -> None:
    with_siblings = {a for a, _ in sibling_pairs()}
    counts = {name: len(found) for name, found in sibling_contracts().items()}
    missing = sorted(with_siblings - counts.keys())
    repeated = sorted(name for name, count in counts.items() if count > 1)
    stray = sorted(counts.keys() - with_siblings)
    assert (missing, repeated, stray) == ([], [], []), (
        f"兄弟の契約が無いサブパッケージ: {missing}\n"
        f"兄弟の契約が二つ以上あるサブパッケージ: {repeated}\n"
        f"兄弟の無いサブパッケージの契約: {stray}"
    )


def test_sibling_contracts_forbid_only_same_layer_siblings() -> None:
    siblings: dict[str, set[str]] = {}
    for a, b in sibling_pairs():
        siblings.setdefault(a, set()).add(f"jigane.{b}")
    foreign = {
        name: sorted(set(found[0]["forbidden_modules"]) - siblings.get(name, set()))
        for name, found in sibling_contracts().items()
    }
    foreign = {name: modules for name, modules in foreign.items() if modules}
    assert foreign == {}, f"同じ層の兄弟でないものが禁止に入っている: {foreign}"


def test_each_sibling_dependency_is_either_forbidden_or_imported() -> None:
    contracts = sibling_contracts()
    allowed_without_import: list[str] = []
    forbidden_but_imported: list[str] = []
    for a, b in sibling_pairs():
        forbidden = f"jigane.{b}" in contracts[a][0]["forbidden_modules"]
        imported = directly_imports(a, b)
        if not forbidden and not imported:
            allowed_without_import.append(f"{a} -> {b}")
        elif forbidden and imported:
            forbidden_but_imported.append(f"{a} -> {b}")
        else:
            # 禁止していて import が無い組と、禁止せず import がある組は、宣言と実際が合っている
            continue
    assert (allowed_without_import, forbidden_but_imported) == ([], []), (
        "import していないのに許したままの組（forbidden_modules へ足す）: "
        f"{allowed_without_import}\n"
        "import しているのに禁止に入っている組（forbidden_modules から外す）: "
        f"{forbidden_but_imported}"
    )


SUBPACKAGE_SECTION = re.compile(
    r"^## サブパッケージ\n(.*?)(?=^## |\Z)", flags=re.DOTALL | re.MULTILINE
)
SUBPACKAGE_ROW = re.compile(r"^\|\s*\[`(\w+)`\]\(", flags=re.MULTILINE)


def subpackage_table_names() -> list[str]:
    """README の「## サブパッケージ」の節の表から、名前の列を上から順に読む。"""
    section = SUBPACKAGE_SECTION.search(README.read_text(encoding="utf-8"))
    assert section is not None, "README に「## サブパッケージ」の節が無い"
    names = SUBPACKAGE_ROW.findall(section.group(1))
    assert names != [], "「## サブパッケージ」の表から名前が一つも取れない"
    return names


def test_subpackage_table_lists_every_subpackage() -> None:
    names = subpackage_table_names()
    assert len(names) == len(set(names)), f"表に重複した名前がある: {names}"
    assert set(names) == subpackages(), (
        f"表にだけある: {sorted(set(names) - subpackages())}\n"
        f"表に無い: {sorted(subpackages() - set(names))}"
    )


def test_subpackage_table_is_in_alphabetical_order() -> None:
    names = subpackage_table_names()
    assert names == sorted(names), f"表の名前がアルファベット順でない: {names}"
