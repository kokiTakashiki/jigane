import pytest

from jigane import __version__
from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK, EXIT_PROCESSING_FAILURE


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--version"])
    assert raised.value.code == EXIT_OK
    assert __version__ in capsys.readouterr().out


@pytest.mark.parametrize("name", ["7", "0128"])
def test_new_files_rejects_a_digits_only_feature_name(
    capsys: pytest.CaptureFixture[str], name: str
) -> None:
    exit_code = main(["new", "files", "src", "--name", name])
    assert exit_code == EXIT_PROCESSING_FAILURE
    assert "保存先が重なります" in capsys.readouterr().err
