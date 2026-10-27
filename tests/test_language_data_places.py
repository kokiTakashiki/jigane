from collections.abc import Iterator
from pathlib import Path

import pytest
from language_plugin_support import Installer, enabled_of, entry_points_in, failure_of, states_of

from jigane.flaky.catalog import EquivalentState, load_catalog
from jigane.languages import grammars, queries, registry
from jigane.languages.data_places import BUILTIN_QUERIES_DIR
from jigane.languages.plugins import ReasonKind, discover


@pytest.fixture(autouse=True)
def _fresh_query_cache() -> Iterator[None]:
    """_load は結果を覚えるので、前後で捨てる。

    差し替えた文法で読んだ結果が他の試験へ漏れないようにする。
    """
    queries._load.cache_clear()
    yield
    queries._load.cache_clear()


_QUERY_BODY = '[[query]]\nname = "calls"\nquery = """(call) @call"""\n'
_FLAKY_BODY = '[[query]]\nid = "FLK-ASYNC-WAIT"\nquery = """(call) @hit"""\n'


def _data_dir(
    root: Path,
    *,
    queries_names: tuple[str, ...] = (),
    flaky_names: tuple[str, ...] = (),
) -> Path:
    data = root / "data"
    data.mkdir(exist_ok=True)
    for names, parts, body in (
        (queries_names, ("languages", "queries"), _QUERY_BODY),
        (flaky_names, ("flaky", "queries"), _FLAKY_BODY),
    ):
        for name in names:
            file = data.joinpath(*parts, f"{name}.toml")
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(body, encoding="utf-8")
    return data


def _enable(tmp_path: Path, name: str) -> None:
    registry.set_states(
        discover(entry_points_in(tmp_path), enabled_of(name), registry.builtin_plugins())
    )


def test_external_queries_are_read_from_the_plugin_data_place(
    install: Installer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _data_dir(tmp_path, queries_names=("snake",))
    install("snake", data_dir=data)
    _enable(tmp_path, "snake")
    places = registry.query_places()
    assert places[0] == BUILTIN_QUERIES_DIR
    assert places[1:] == (data / "languages" / "queries",)
    # 外の言語の文法は本体の文法パックに無いので、読み込みだけを python の文法で代える
    python_language = grammars.get_language(grammars.BuiltinGrammar("python"))
    monkeypatch.setattr(queries, "get_language", lambda _target: python_language)
    assert "calls" in queries.load_queries("snake", places)


def test_builtin_query_file_wins_over_an_external_one_with_the_same_name(tmp_path: Path) -> None:
    data = _data_dir(tmp_path, queries_names=("python",))
    external = data / "languages" / "queries"
    external.joinpath("python.toml").write_text(
        '[[query]]\nname = "external_only"\nquery = """(call) @call"""\n', encoding="utf-8"
    )
    loaded = queries.load_queries("python", (BUILTIN_QUERIES_DIR, external))
    assert "external_only" not in loaded
    assert "calls" in loaded


def test_missing_query_file_is_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        queries.load_queries("nowhere", (BUILTIN_QUERIES_DIR, tmp_path))


def test_external_flaky_catalog_is_read_and_unverified(
    install: Installer,
    tmp_path: Path,
) -> None:
    data = _data_dir(tmp_path, flaky_names=("snake",))
    install("snake", data_dir=data)
    _enable(tmp_path, "snake")
    item = next(i for i in load_catalog() if i.id == "FLK-ASYNC-WAIT")
    specs = [spec for spec in item.queries if spec.language == "snake"]
    assert len(specs) == 1
    assert specs[0].equivalent.state is EquivalentState.UNVERIFIED


def test_external_language_without_flaky_data_is_unsupported_not_an_error(
    install: Installer,
    tmp_path: Path,
) -> None:
    install("snake", data_dir=_data_dir(tmp_path, queries_names=("snake",)))
    _enable(tmp_path, "snake")
    catalog = load_catalog()
    assert all(spec.language != "snake" for item in catalog for spec in item.queries)
    assert {spec.language for item in catalog for spec in item.queries} >= {"python", "java"}


def test_external_language_without_any_data_is_unsupported_not_an_error(
    install: Installer,
    tmp_path: Path,
) -> None:
    install("snake")
    _enable(tmp_path, "snake")
    assert all(spec.language != "snake" for item in load_catalog() for spec in item.queries)


@pytest.mark.parametrize(("kind", "name"), [("queries", "python"), ("flaky", "java")])
def test_external_file_named_like_a_builtin_one_is_a_validation_error(
    install: Installer,
    tmp_path: Path,
    kind: str,
    name: str,
) -> None:
    data = _data_dir(
        tmp_path,
        queries_names=(name,) if kind == "queries" else (),
        flaky_names=(name,) if kind == "flaky" else (),
    )
    install("snake", data_dir=data)
    failure = failure_of(states_of(tmp_path, ("snake",)), "snake")
    assert failure.reason_kind is ReasonKind.VALIDATION_ERROR
    assert f"{name}.toml" in failure.detail
    assert "本体のものが使われます" in failure.detail


def test_failed_external_with_a_clashing_file_adds_no_place(
    install: Installer,
    tmp_path: Path,
) -> None:
    install("snake", data_dir=_data_dir(tmp_path, queries_names=("python",)))
    _enable(tmp_path, "snake")
    assert len(registry.query_places()) == 1
    assert len(registry.flaky_places()) == 1


def test_external_label_comes_from_the_plugin(
    install: Installer,
    tmp_path: Path,
) -> None:
    install("snake")
    _enable(tmp_path, "snake")
    assert registry.label_for("snake") == "Fake"
    assert registry.label_for("csharp") == "C#"
