"""Windows で MCP の一時の設定ファイルが利用者の一時のディレクトリに作られ、消えること。

既定の testpaths の外に置き、Windows の CI の job だけが明示して走らせる。
権限は絞らず既定の ACL に頼るので、ここで固定するのは置き場と後始末だけ。
"""

import json
import tempfile
from pathlib import Path

from jigane.skills.mcp import McpSelection, McpServer


def test_config_file_lives_under_the_user_temp_directory_and_is_removed() -> None:
    selection = McpSelection((McpServer("mobile-sec", {"command": "x"}, None),))

    with selection.config_file() as path:
        assert path is not None
        assert Path(tempfile.gettempdir()).resolve() in path.resolve().parents
        assert json.loads(path.read_text(encoding="utf-8")) == selection.config_content()

    assert not path.exists()


def test_no_file_is_made_without_servers() -> None:
    with McpSelection.none().config_file() as path:
        assert path is None
