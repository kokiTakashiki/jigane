import zipfile
from email.parser import Parser
from pathlib import Path

from dev_launch import run_tool

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_wheel_declares_mit_and_bundles_its_license(tmp_path: Path) -> None:
    run_tool(["uv", "build", "--wheel", "-o", str(tmp_path)], cwd=PROJECT_ROOT)
    [wheel] = tmp_path.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")

    assert Parser().parsestr(metadata)["License-Expression"] == "MIT"
    assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)
