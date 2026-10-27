"""外の言語の試験の支え。偽の言語を site に置く fixture と、発見の結果を引く関数を持つ。"""

import sys
from collections.abc import Callable, Iterator
from importlib.metadata import EntryPoint, distributions
from pathlib import Path

import pytest

from jigane.languages import registry
from jigane.languages.plugins import (
    EnabledLanguage,
    LanguageReady,
    LanguageUnavailable,
    discover,
)

_MODULE_TEMPLATE = """\
from pathlib import Path

from jigane.languages.plugin import ApiVersion, Builtin, Distribution, GrammarSpec, LanguagePlugin

Path({marker!r}).write_text("imported", encoding="utf-8")


class Adapter:
    name = {adapter_name!r}
    grammar = {grammar!r}
    suffixes = {suffixes_expr}
    comment_node_types = frozenset()
    import_node_types = frozenset()
    test_directory_names = ()
    test_file_patterns = ()
    build_file_patterns = ()
{declarations}


def make():
    {factory_body}
"""

_COMPLETE = "    def declarations(self, root_node, source):\n        return []"
_RETURN = """return LanguagePlugin(
        origin={origin},
        api_version=ApiVersion({major}, {minor}),
        adapter=Adapter(),
        label="Fake",
        grammar={spec_expr},
        data={data_expr},
    )"""

Installer = Callable[..., Path]


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Installer]:
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.syspath_prepend(str(site))
    installed: list[str] = []

    def _install(
        name: str,
        *,
        suffixes: tuple[str, ...] = (".fake",),
        adapter_name: str | None = None,
        major: int = 1,
        minor: int = 0,
        complete: bool = True,
        raises: bool = False,
        origin: str | None = None,
        dist: str | None = None,
        suffixes_expr: str | None = None,
        data_dir: Path | None = None,
        grammar: str = "python",
        spec_expr: str = 'GrammarSpec(pack_version="1", hashes={})',
    ) -> Path:
        module = f"fakelang_{name}"
        marker = tmp_path / f"{module}.marker"
        dist = dist or f"{module}-dist"
        installed.append(module)
        body = (
            'raise RuntimeError("boom")'
            if raises
            else _RETURN.format(
                origin=origin or f'Distribution("{dist}", "1.0")',
                major=major,
                minor=minor,
                spec_expr=spec_expr,
                data_expr=f"Path({str(data_dir)!r})" if data_dir is not None else "None",
            )
        )
        source = _MODULE_TEMPLATE.format(
            marker=str(marker),
            adapter_name=adapter_name or name,
            grammar=grammar,
            suffixes_expr=suffixes_expr or f"frozenset({sorted(suffixes)!r})",
            declarations=_COMPLETE if complete else "",
            factory_body=body,
        )
        (site / f"{module}.py").write_text(source, encoding="utf-8")
        info = site / f"{dist.replace('-', '_')}-1.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {dist}\nVersion: 1.0\n", encoding="utf-8"
        )
        (info / "entry_points.txt").write_text(
            f"[jigane.languages]\n{name} = {module}:make\n", encoding="utf-8"
        )
        return marker

    yield _install
    for module in installed:
        sys.modules.pop(module, None)


def entry_points_in(tmp_path: Path) -> list[EntryPoint]:
    site = str(tmp_path / "site")
    return [
        entry_point
        for dist in distributions(path=[site])
        for entry_point in dist.entry_points
        if entry_point.group == "jigane.languages"
    ]


def enabled_of(*names: str) -> tuple[EnabledLanguage, ...]:
    return tuple(EnabledLanguage(name, f"fakelang_{name}-dist", "1.0") for name in names)


def states_of(tmp_path: Path, names: tuple[str, ...]) -> dict[str, object]:
    states = discover(entry_points_in(tmp_path), enabled_of(*names), registry.builtin_plugins())
    return {(s.plugin.adapter.name if isinstance(s, LanguageReady) else s.name): s for s in states}


def failure_of(states: dict[str, object], name: str) -> LanguageUnavailable:
    state = states[name]
    assert isinstance(state, LanguageUnavailable)
    return state
