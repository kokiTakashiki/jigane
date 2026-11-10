# jigane.storage

データの置き場の読み書き、保存物の形の検査、読み書きの失敗を受け持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `store` | データホスト（根は新しく作るときだけ 0700 にし、既にある根の権限は変えません）とレビューの識別子を持ちます |
| `locking` | OS のファイルロックと、レビューのディレクトリの状態を排他にする `review_lock` を持ちます |
| `atomic_write` | 一時のファイルへ書いてから置き換える原子的な書き込みを持ちます |
| `review_creation` | レビューの作成の受け付け、仮の置き場の据え付け、保存の宛先を持ちます |
| `review_access` | 既にあるレビューの保存したファイルの読みと、状態を排他で書く `locked_review` を持ちます |
| `store_format` | 保存物の版の判別と、古い版から現行の版への上げ方を持ちます |
| `store_suffixes` | 保存物のファイルの拡張子の一覧を持ちます。設計書の読み取りとコードの読み取りが同じ一覧を引きます |
| `review_error` | 保存物の読み書きの失敗を表す例外を持ちます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：なし

読まれるサブパッケージ：`commands`、`design`、`findings`、`flaky`、`judgment_map`、`languages`、`publishing`、`pull_request_context`、`reviews`、`skills`、`static_analysis`

<!-- END GENERATED: dependencies -->
