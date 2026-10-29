"""examples/jigane-language-kotlin を、一時の site に入れた外のパッケージとして受け入れる。

Kotlin の文法での解析は、文法の取得の仕組みと合わせて別の段で試験する。
"""

import os
import sys
import tomllib
from collections.abc import Iterator
from importlib.metadata import EntryPoint, distributions
from pathlib import Path

import pytest

from jigane.languages import registry
from jigane.languages.plugins import EnabledLanguage, LanguageReady, discover, validate_plugin
from jigane.skills.language_support import support_of

EXAMPLE = Path(__file__).parent.parent / "examples" / "jigane-language-kotlin"
DISTRIBUTION = "jigane-language-kotlin"


@pytest.fixture
def entry_point(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[EntryPoint]:
    project = tomllib.loads((EXAMPLE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    site = tmp_path / "site"
    info = site / f"{DISTRIBUTION.replace('-', '_')}-{project['version']}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {project['version']}\n",
        encoding="utf-8",
    )
    group, items = next(iter(project["entry-points"].items()))
    lines = "".join(f"{name} = {target}\n" for name, target in items.items())
    (info / "entry_points.txt").write_text(f"[{group}]\n{lines}", encoding="utf-8")
    # 実際の配布と同じく RECORD に載せる。ソースは site の外にあるので相対パスで指す
    sources = [
        os.path.relpath(source, site)
        for source in (EXAMPLE / "jigane_language_kotlin").glob("*.py")
    ]
    names = [f"{info.name}/METADATA", f"{info.name}/entry_points.txt", *sources]
    (info / "RECORD").write_text(
        "".join(f"{n},,\n" for n in [*names, f"{info.name}/RECORD"]), encoding="utf-8"
    )
    # ソースの置き場と dist-info の site を、import と importlib.metadata が引ける所に足す
    monkeypatch.syspath_prepend(str(EXAMPLE))
    monkeypatch.syspath_prepend(str(site))
    (found,) = (
        entry_point
        for dist in distributions(path=[str(site)])
        for entry_point in dist.entry_points
        if entry_point.group == "jigane.languages"
    )
    yield found
    sys.modules.pop("jigane_language_kotlin", None)


def _approved(entry_point: EntryPoint) -> EnabledLanguage:
    assert entry_point.dist is not None
    return EnabledLanguage.approve("kotlin", entry_point.dist)


def test_example_passes_validation(entry_point: EntryPoint) -> None:
    assert entry_point.name == "kotlin"
    assert validate_plugin(entry_point, entry_point.load()()) is None


def test_example_is_ready_once_enabled(entry_point: EntryPoint) -> None:
    states = discover(
        [entry_point],
        [_approved(entry_point)],
        registry.builtin_plugins(),
    )
    ready = {s.plugin.adapter.name: s for s in states if isinstance(s, LanguageReady)}
    kotlin = ready["kotlin"].plugin
    assert kotlin.label == "Kotlin"
    assert kotlin.adapter.suffixes == frozenset({".kt", ".kts"})


def test_example_supports_no_language_figure(entry_point: EntryPoint) -> None:
    states = discover(
        [entry_point],
        [_approved(entry_point)],
        registry.builtin_plugins(),
    )
    kotlin = next(
        s.plugin
        for s in states
        if isinstance(s, LanguageReady) and s.plugin.adapter.name == "kotlin"
    )
    support = support_of(kotlin)
    assert support.supported_figures() == ()
    assert len(support.unsupported_figures()) == len(support.figures) > 0
