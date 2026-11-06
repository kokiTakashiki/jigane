"""モデルごとの単価と、プロンプトの大きさからの費用の換算。"""

import math
from dataclasses import dataclass


def estimate_input_tokens(text_bytes: int) -> int:
    # 日本語、英語、コードが混じるため係数 3 は仮置きである。後の段で実測して直す。
    return math.ceil(text_bytes / 3)


@dataclass(frozen=True)
class PriceTier:
    """プロンプトのトークン数の段階ごとの単価（USD / 100 万トークン）。"""

    # この段階に入るプロンプトのトークン数の上限（含む）。None は上限なし
    up_to_prompt_tokens: int | None
    cache_write_1h: float
    # キャッシュの読み出し（cache hits）の単価
    cache_read: float
    output: float


@dataclass(frozen=True)
class ModelPrices:
    # 入力の単価（USD / 100 万トークン）。プロンプトの大きさの見積もりと安全弁に使う
    input: float
    # 1 時間のキャッシュの書き込みと出力の単価。上限の小さい順に並べる
    tiers: tuple[PriceTier, ...]


# モデルごとの単価の表。出典 https://platform.claude.com/docs/en/about-claude/pricing
# 2026-11-04 に本文で確かめた。表に無いモデルには、表の最高の単価を仮に当てる。
# Haiku 5.5 は、プロンプトが 100,000 トークンを超える起動の入力全体と出力全体に高い単価が掛かる
MODEL_PRICES: dict[str, ModelPrices] = {
    "claude-sonnet-5-5": ModelPrices(2.00, (PriceTier(None, 4.00, 0.10, 10.00),)),
    "claude-opus-5-5": ModelPrices(4.00, (PriceTier(None, 8.00, 0.20, 20.00),)),
    "claude-haiku-5-5": ModelPrices(
        0.10, (PriceTier(100_000, 0.20, 0.01, 0.50), PriceTier(None, 1.00, 0.05, 2.50))
    ),
    "claude-fable-5-1": ModelPrices(10.00, (PriceTier(None, 20.00, 0.25, 50.00),)),
}

# 名前を変えない口。値は MODEL_PRICES から導く
INPUT_USD_PER_MILLION_TOKENS: dict[str, float] = {
    model: prices.input for model, prices in MODEL_PRICES.items()
}


@dataclass(frozen=True)
class InputCost:
    usd: float
    price_per_million: float
    # 表に無いモデルに、表の最高の単価を当てたか
    assumed_price: bool


def input_cost_usd(model: str, input_tokens: int) -> InputCost:
    price = INPUT_USD_PER_MILLION_TOKENS.get(model)
    assumed = price is None
    if price is None:
        # 表の手入れに安全弁が左右されないよう、安全側に倒して最高の単価で見積もる
        price = max(INPUT_USD_PER_MILLION_TOKENS.values())
    return InputCost(input_tokens * price / 1_000_000, price, assumed)


# 実際の課金に合わせた見積もりの係数。方針の起動の大きなプロンプトの実測（起動 3 本）で、
# 2.24〜2.30 バイトが 1 トークンだった。
BILLED_BYTES_PER_TOKEN = 2.25


def billed_input_tokens(text_bytes: int) -> int:
    return math.ceil(text_bytes / BILLED_BYTES_PER_TOKEN)


def tier_prices(model: str, prompt_tokens: int) -> tuple[PriceTier, bool]:
    """単価の段階を選ぶ。表に無いモデルは、全段階の最高の単価を各欄に持つ段階と True を返す。"""
    prices = MODEL_PRICES.get(model)
    if prices is None:
        # 表の手入れに安全弁が左右されないよう、全段階の最高の単価で見積もる
        every = [tier for each in MODEL_PRICES.values() for tier in each.tiers]
        highest = PriceTier(
            None,
            max(tier.cache_write_1h for tier in every),
            max(tier.cache_read for tier in every),
            max(tier.output for tier in every),
        )
        return highest, True
    tier = next(
        item
        for item in prices.tiers
        if item.up_to_prompt_tokens is None or prompt_tokens <= item.up_to_prompt_tokens
    )
    return tier, False


def billed_launch_cost(model: str, prompt_tokens: int, output_tokens: int) -> "InputCost":
    """入力を 1 時間のキャッシュの書き込みの単価、見込みの出力を出力の単価で見積もる。"""
    tier, assumed = tier_prices(model, prompt_tokens)
    # 浮動小数の誤差で上限ちょうどの判定が揺れないよう、百万分の一 USD の下で丸める
    usd = round((prompt_tokens * tier.cache_write_1h + output_tokens * tier.output) / 1_000_000, 9)
    return InputCost(usd, tier.cache_write_1h, assumed)


def assumed_price_notice(model: str, cost: InputCost) -> str:
    return (
        f"注: {model} は単価の表に無いため、表の最高の単価（{cost.price_per_million:g} USD / "
        "100 万トークン）で見積もりました"
    )
