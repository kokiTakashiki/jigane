# jigane.storage

データの置き場の読み書き、保存物の形の検査、読み書きの失敗を受け持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `store` | データホスト、レビューの識別子、排他、原子的な書き込みを持ちます |
| `store_format` | 保存物の版の判別と、古い版から現行の版への上げ方を持ちます |
| `review_error` | 保存物の読み書きの失敗を表す例外を持ちます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：なし

読まれるサブパッケージ：`commands`、`design`、`findings`、`flaky`、`judgment_map`、`languages`、`publishing`、`pull_request_context`、`reviews`、`skills`、`static_analysis`

<!-- END GENERATED: dependencies -->
