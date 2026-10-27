"""雛形の要の文が、描いた本文から消えたり変わったりしたら落ちる試験。

要の文の一覧は tests/fixtures/prompt_key_sentences.toml。意図して変えるときは一覧を同じ変更で直す。
"""

import tomllib
from importlib import resources
from pathlib import Path

import pytest

from jigane.skills import adversarial, macro_shared, shared_rules

KEY_SENTENCES_PATH = Path(__file__).parent / "fixtures" / "prompt_key_sentences.toml"

# 発見役、反証役、判定役のどれでもない雛形。要の文の対象は、レビューの役のふるまいを決める雛形だけ
NOT_REVIEWER_TEMPLATES = frozenset({"bundle_disclosed", "themes"})


def key_sentences() -> dict[str, list[str]]:
    table = tomllib.loads(KEY_SENTENCES_PATH.read_text(encoding="utf-8"))
    return {name: list(entry["sentences"]) for name, entry in table.items()}


def existing_templates() -> set[str]:
    prompts = resources.files("jigane.skills").joinpath("prompts")
    names = {
        entry.name.removesuffix(".md") for entry in prompts.iterdir() if entry.name.endswith(".md")
    }
    return names - NOT_REVIEWER_TEMPLATES


def rendered_body(name: str) -> str:
    """共有の部品を差し込んだ後の本文。実行時の差し込みと同じ部品を、同じ関数で差し込む。"""
    parts = macro_shared.prompt_parts()
    match name:
        case "macro_standards":
            return parts["standards"]
        case "macro_history_rule":
            return parts["history_rule"]
        case _:
            return adversarial.load_prompt(name).safe_substitute(
                common=adversarial.common_prompt(),
                **parts,
                **shared_rules.values_for(name),
            )


def sentence_cases() -> list[tuple[str, str]]:
    return [
        (name, sentence) for name, sentences in key_sentences().items() for sentence in sentences
    ]


def test_template_names_match_the_list() -> None:
    listed = set(key_sentences())
    existing = existing_templates()
    assert listed == existing, (
        f"雛形の名前が一覧と食い違う。一覧に無い雛形: {sorted(existing - listed)}、"
        f"雛形が無い一覧の項目: {sorted(listed - existing)}"
    )


def test_every_template_has_key_sentences() -> None:
    empty = sorted(name for name, sentences in key_sentences().items() if not sentences)
    assert empty == [], f"要の文が空の雛形: {empty}"


def test_key_sentences_are_unique_within_a_template() -> None:
    duplicated = sorted(
        {
            (name, sentence)
            for name, sentences in key_sentences().items()
            for sentence in sentences
            if sentences.count(sentence) > 1
        }
    )
    assert duplicated == [], f"同じ雛形に重複した要の文: {duplicated}"


CASES = sentence_cases()
# 雛形ごとの通し番号（micro-1、micro-2 ...）。一覧の途中に足しても、ほかの雛形の ID は動かない
CASE_IDS = [
    f"{name}-{index}"
    for name, sentences in key_sentences().items()
    for index in range(1, len(sentences) + 1)
]


@pytest.mark.parametrize(("name", "sentence"), CASES, ids=CASE_IDS)
def test_key_sentence_is_in_the_rendered_prompt_exactly_once(name: str, sentence: str) -> None:
    count = rendered_body(name).count(sentence)
    assert count == 1, (
        f"雛形 {name}.md の要の文が、描いた本文に {count} 回現れる（ちょうど一回であるべき）: "
        f"「{sentence}」。消えたか変わったなら、意図した変更として "
        "tests/fixtures/prompt_key_sentences.toml と決定を同じ変更で直す。"
        "二回以上なら、前後を含めて一意に決まる長さに伸ばす"
    )
