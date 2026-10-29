"""mcp の設定の一時ファイルの権限 0o600 を確かめる試験。POSIX の権限に頼る。

既定の testpaths の外に置き、macOS と Linux のゲート（Makefile の test）だけが走らせる。
Windows のゲート（justfile の test）は走らせない。置き場の違いで走る OS が決まる。
"""

from pathlib import Path

from fakes import settings_for
from test_security import ConfigCapture, write_mcp

from jigane.reviews.lifecycle import create_feature_review
from jigane.skills import security
from jigane.skills.assembly import AssemblyInput
from jigane.skills.mcp import McpSelection


def test_mcps_temporary_file_is_private_to_the_owner(project: Path) -> None:
    review = create_feature_review(project, ["src"], "p")
    write_mcp(("mobile-sec",))
    fake = ConfigCapture()
    security.run_security(
        AssemblyInput.load(review), settings_for(fake), McpSelection.load(("mobile-sec",))
    )
    assert fake.seen
    for _path, _content, mode in fake.seen:
        assert mode == 0o600
