"""mcp.json の権限の警告の試験。POSIX だけの置き場に置く。

Windows の権限は mode のビットに現れないので、jigane は Windows で権限を確かめない。
置き場の違いで走る OS が決まる。
"""

import logging

import pytest
from test_security import write_mcp

from jigane.skills.mcp import McpSelection, mcp_config_path


@pytest.mark.parametrize(("mode", "warning_count"), [(0o600, 0), (0o640, 1), (0o666, 1)])
def test_load_warns_when_mcp_json_is_broadly_accessible(
    caplog: pytest.LogCaptureFixture, mode: int, warning_count: int
) -> None:
    write_mcp(("mobile-sec",))
    mcp_config_path().chmod(mode)
    with caplog.at_level(logging.WARNING, logger="jigane.skills.mcp"):
        selection = McpSelection.load(("mobile-sec",))
    assert selection.names == ("mobile-sec",)
    assert len([m for m in caplog.messages if "権限" in m]) == warning_count
