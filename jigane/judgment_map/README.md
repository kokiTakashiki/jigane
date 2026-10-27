# jigane.judgment_map

判断の地図、変更の宣言、変更の種類、`.jigane.toml` の規則を受け持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `region_table` | 領域の表の保存の形を持ちます |
| `classification` | 変更を種類と判断の要否に分けます。中心は `classify` です |
| `changes` | 二つのツリーから作る統一形式の差分を持ちます |
| `declaration` | 変更の宣言（意図、公開名）の抽出を持ちます |
| `map_kind` | 判断の地図の種類（コードの地図と設計書の地図）を持ちます |
| `allow_annotation` | 外しの注記をコメントから読みます |
| `kinds` | 変更の種別と水準の定義を持ちます |
| `configuration` | `.jigane.toml` の規則を読みます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`git`、`languages`、`markdown`、`preconditions`、`storage`

読まれるサブパッケージ：`commands`、`design`、`flaky`、`policy_materials`、`pull_request_context`、`regex`、`reviews`、`sequence`、`skills`

<!-- END GENERATED: dependencies -->

- `languages` は `judgment_map` を読みません。
