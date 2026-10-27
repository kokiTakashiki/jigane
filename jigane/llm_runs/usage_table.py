"""usage.csv の列と値の定義。書く側（`jigane usage`）と読む側（見積もりの倍率）が共有する。

`jigane usage <レビュー> --csv --no-header`（jigane/commands/subcommands/usage.py）は
行を標準出力に出すだけで、置き場には書かない。
USAGE_CSV の表は、その出力に日付、レビュー、コミットの列を足して手で追記したもの。

commands にも skills の実行にも依存しないため、どちらからも循環 import なしに引ける。
列の並びは保存済みの行が頼る永続の契約で、並びの定義は jigane.commands.subcommands.usage が持つ。
ここが持つのは、列の名前と、役の名前である。
"""

from enum import StrEnum
from pathlib import Path

from jigane.groups.policy_runs import PolicyRole

# 手で追記した使用量の表の、リポジトリの根からの相対の置き場。`jigane usage` はここへ書かない
USAGE_CSV = Path("docs") / "reviews" / "usage.csv"


class UsageField(StrEnum):
    """usage.csv の列名。`jigane usage` が出す全ての列で、値は CSV と JSON の鍵と同じ。

    行末のコメントは列の単位。単位を持たない列（model、effort、calls など）には付けない。
    """

    GROUP = "group"
    ROLE = "role"
    MODEL = "model"
    PROMPT_VERSION = "prompt_version"
    PROMPT_CHARS = "prompt_chars"  # chars
    CALLS = "calls"
    TOTAL_TOKENS = "total_tokens"  # tokens
    TURNS = "turns"
    INPUT_TOKENS = "input_tokens"  # tokens
    CACHE_CREATION_TOKENS = "cache_creation_tokens"  # tokens
    CACHE_READ_TOKENS = "cache_read_tokens"  # tokens
    OUTPUT_TOKENS = "output_tokens"  # tokens
    COST_USD = "cost_usd"  # USD
    DURATION_S = "duration_s"  # s
    FINDINGS_TOTAL = "findings_total"
    USD_PER_MUST_READ = "usd_per_must_read"  # USD
    EFFORT = "effort"
    DISCOVERED = "discovered"
    DISCARDED = "discarded"
    DISCARD_RATIO = "discard_ratio"
    DISCARD_STATE = "discard_state"


# 入力の単価で数える 3 種のトークン。出力のトークンは別の単価なので含めない
INPUT_SIDE_TOKEN_FIELDS = (
    UsageField.INPUT_TOKENS,
    UsageField.CACHE_CREATION_TOKENS,
    UsageField.CACHE_READ_TOKENS,
)

# 方針の役の名前は PolicyRole.label が持つ。廃止した形の役もここにある
ROLE_LABELS: dict[str, str] = {
    "discovery": "発見",
    **{role.value: role.label for role in PolicyRole},
    "rebuttal": "反証",
    "alternative": "代案",
    "defense": "擁護",
    "judge": "判定",
    "themes": "束ね",
}
TOTAL_ROLE = "合計"
