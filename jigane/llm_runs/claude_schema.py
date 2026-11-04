"""claude の `--json-schema` へ渡すスキーマの組み立て。"""

from pydantic import BaseModel, JsonValue


def json_schema_of(model: type[BaseModel]) -> dict[str, JsonValue]:
    """claude の `--json-schema` へ渡すスキーマ。title は除き、$ref は展開する。"""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})
    return _object_without_titles(_object_with_references(schema, definitions))


def _object_with_references(
    node: dict[str, JsonValue], definitions: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    # 入れ子のモデルは $defs への $ref になる。旧のスキーマは入れ子を直に書いていたので展開する
    reference = node.get("$ref")
    if isinstance(reference, str):
        target = definitions[reference.removeprefix("#/$defs/")]
        if isinstance(target, dict):
            # 定義の description はクラスの docstring 由来で、コードの読み手に向けた文。
            # claude に渡すのは、欄に Field(description=...) で明示した文だけ（$ref の隣に載る）
            expanded = _object_with_references(
                {key: value for key, value in target.items() if key != "description"}, definitions
            )
            beside = {key: value for key, value in node.items() if key != "$ref"}
            return {**expanded, **_object_with_references(beside, definitions)}
    return {key: _with_references(value, definitions) for key, value in node.items()}


def _with_references(node: JsonValue, definitions: dict[str, JsonValue]) -> JsonValue:
    if isinstance(node, dict):
        return _object_with_references(node, definitions)
    if isinstance(node, list):
        return [_with_references(item, definitions) for item in node]
    # 葉の値（型名や数値）はそのまま返す
    return node


def _object_without_titles(node: dict[str, JsonValue]) -> dict[str, JsonValue]:
    # 文字列の title だけが説明用。値が辞書の title は、title という名の項目の定義
    return {
        key: _without_titles(value)
        for key, value in node.items()
        if not (key == "title" and isinstance(value, str))
    }


def _without_titles(node: JsonValue) -> JsonValue:
    if isinstance(node, dict):
        return _object_without_titles(node)
    if isinstance(node, list):
        return [_without_titles(item) for item in node]
    # 葉の値（型名や数値）はそのまま返す
    return node
