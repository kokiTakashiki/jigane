"""群の見積もりと非対話実行の上限判定を、偽の起動で照合する。"""

import json
import math
from pathlib import Path

import pytest
from test_security import SecurityFake, write_mcp

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_ESTIMATE_OVER_LIMIT, EXIT_OK
from jigane.reviews.lifecycle import create_feature_review

pytestmark = pytest.mark.usefixtures("selected_agent")


@pytest.mark.parametrize("names", [(), ("refs",), ("refs", "baseline")])
def test_claude_mcp_group_estimate_matches_debug_run(
    names: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "sample.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    review = create_feature_review(root, ["sample.py"], name="mcp-cost")
    write_mcp(("refs", "baseline", "unselected"))
    options = ["--mcps", *names] if names else []
    args = [str(review.id), "security", "--json", *options]
    fake = SecurityFake({1: []})

    assert main(["estimate", *args], invoke=fake) == EXIT_OK
    estimate = json.loads(capsys.readouterr().out)
    assert fake.calls == []
    expected = estimate["expected"]
    # Sonnet の 1 時間キャッシュ書込と出力で各 4,000 トークン、1 台につき 0.056 USD。
    assert expected["discovery_estimate_usd"] == pytest.approx(
        estimate["input_cost_usd"] + 0.056 * len(names) * len(estimate["prompt_bytes"])
    )
    amount = expected["total_usd"]
    below = math.nextafter(amount, 0.0)
    debug = ["debug", "run", *args, "--yes", "--max-usd"]

    assert main([*debug, str(below)], invoke=fake) == EXIT_ESTIMATE_OVER_LIMIT
    stopped = json.loads(capsys.readouterr().out)
    assert stopped["stopped"] == "estimate_exceeds_max_usd"
    assert stopped["estimated_usd"] == amount
    assert fake.calls == []

    assert main([*debug, str(amount)], invoke=fake) == EXIT_OK
    result = json.loads(capsys.readouterr().out)
    assert result["estimated_usd"] == result["max_usd"] == amount
    [call] = fake.calls
    assert call.mcp_server_names == names
