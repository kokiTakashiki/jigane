"""言語ごとの tree-sitter の query を、データのファイルから読む。"""

import tomllib
from collections.abc import Sequence
from functools import cache
from importlib.resources.abc import Traversable

from tree_sitter import Query

from jigane.languages.grammars import get_language


def load_queries(grammar: str, places: Sequence[Traversable] | None = None) -> dict[str, Query]:
    """名前から、コンパイル済みの query への対応。ファイルの無い言語では FileNotFoundError。

    places は置き場の一覧で、先に並んだ置き場のファイルが勝つ。省略時は登録の一覧を使う。
    """
    if places is None:
        # registry は各言語のアダプタを import し、アダプタがこのモジュールを import する
        from jigane.languages import registry  # noqa: PLC0415 循環を避ける（上のコメント）

        places = registry.query_places()
    return _load(grammar, tuple(places))


@cache
def _load(grammar: str, places: tuple[Traversable, ...]) -> dict[str, Query]:
    for place in places:
        file = place.joinpath(f"{grammar}.toml")
        if file.is_file():
            text = file.read_text(encoding="utf-8")
            break
    else:
        raise FileNotFoundError(f"{grammar}.toml が置き場にありません: {[str(p) for p in places]}")
    # registry は各言語のアダプタを import し、アダプタがこのモジュールを import する
    from jigane.languages import registry  # noqa: PLC0415 循環を避ける（上のコメント）

    # 宣言は states() が一度だけ求めて固定するので、文法名の鍵で宣言違いの結果は混ざらない
    language = get_language(registry.grammar_target_named(grammar))
    return {
        entry["name"]: Query(language, entry["query"]) for entry in tomllib.loads(text)["query"]
    }
