"""群から見積もりへの対応。

群で見積もりとプロンプトを分ける分岐は `launch_prompts.group_launch` の match だけに置く。
"""

from jigane.skills.estimate_result import Estimate
from jigane.skills.launch_prompts import LaunchInput, group_launch


def estimate_of(launch_input: LaunchInput) -> Estimate:
    """budget は実行が使う上限の組。実行と同じシャードの数にするため、実行の入口は必ず渡す。"""
    return group_launch(launch_input).estimate()
