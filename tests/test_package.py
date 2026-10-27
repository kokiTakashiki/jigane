import importlib
import io
import os
import shutil
import sys
import zipfile
from configparser import ConfigParser
from pathlib import Path

import pytest
from dev_launch import run_tool

from jigane.commands import _write_streams_as_utf8

ROOT = Path(__file__).parent.parent
PACKAGE = ROOT / "jigane"


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> zipfile.ZipFile:
    uv = shutil.which("uv")
    assert uv is not None, "パッケージの検査には uv が要る"
    out = tmp_path_factory.mktemp("dist")
    # ビルドのバックエンドは just setup の uv sync が取得済みなので、ネットワークに出ない
    run_tool([uv, "build", "--wheel", "--offline", "--quiet", "--out-dir", str(out), str(ROOT)])
    (built,) = out.glob("*.whl")
    return zipfile.ZipFile(built)


def test_wheel_carries_every_package_file(wheel: zipfile.ZipFile) -> None:
    # 画面の静的ファイルや検出のクエリの TOML は import されないので、漏れても開発の環境では
    # 気づかない
    files = {
        f"jigane/{path.relative_to(PACKAGE).as_posix()}"
        for path in PACKAGE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert files - set(wheel.namelist()) == set()


def test_wheel_leaves_out_the_examples(wheel: zipfile.ZipFile) -> None:
    # examples/ は配布物ではない。言語プラグインの例が本体の wheel に混ざらない
    leaked = [n for n in wheel.namelist() if n.startswith(("examples/", "jigane_language_"))]
    assert leaked == []


def test_wheel_leaves_out_the_review_fakes(wheel: zipfile.ZipFile) -> None:
    # 試験の補助は tests/review_fakes.py にあり、配布物には入らない
    leaked = [n for n in wheel.namelist() if "_testing" in n or "review_fakes" in n]
    assert leaked == []


def test_wheel_leaves_out_the_themes_data(wheel: zipfile.ZipFile) -> None:
    leaked = [n for n in wheel.namelist() if "themes_data" in n]
    assert leaked == []


def test_console_script_reaches_the_cli(wheel: zipfile.ZipFile) -> None:
    (entry_points,) = (n for n in wheel.namelist() if n.endswith(".dist-info/entry_points.txt"))
    parser = ConfigParser()
    parser.read_string(wheel.read(entry_points).decode("utf-8"))
    module, _, attribute = parser["console_scripts"]["jigane"].partition(":")
    main = getattr(importlib.import_module(module), attribute)
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0


def test_module_entry_prints_help() -> None:
    # `python -m jigane` でも CLI が起動する
    assert "usage" in run_tool([sys.executable, "-m", "jigane", "--help"])


def test_module_entry_prints_japanese_help_when_the_stream_encoding_is_ascii() -> None:
    # Windows のパイプは標準出力を cp1252 にする。入口が UTF-8 に揃えないと日本語の --help が落ちる
    # git 以外では env が環境全体の置き換えになる。Windows は SYSTEMROOT などが無いと Python が
    # 起動しないので、継承した環境に足す。PYTHONPATH は別の jigane を読ませるので除く
    inherited = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
    output = run_tool(
        [sys.executable, "-m", "jigane", "--help"],
        env={**inherited, "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"},
    )
    assert "usage" in output


def test_stream_setup_switches_a_non_utf8_text_stream_to_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    _write_streams_as_utf8()
    assert stream.encoding == "utf-8"


def test_stream_setup_leaves_a_utf8_text_stream_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 既に utf-8 なら reconfigure を呼ばない。reconfigure は書き込み済みの出力を flush するため、
    # 呼ばれなかったことを同じオブジェクトで確かめる
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    reconfigured: list[object] = []
    monkeypatch.setattr(stream, "reconfigure", lambda **kwargs: reconfigured.append(kwargs))
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    _write_streams_as_utf8()
    assert sys.stdout is stream
    assert stream.encoding == "utf-8"
    assert reconfigured == []


def test_stream_setup_leaves_a_stream_that_is_not_a_text_wrapper_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    _write_streams_as_utf8()
    assert sys.stdout is stream
    assert stream.encoding is None
