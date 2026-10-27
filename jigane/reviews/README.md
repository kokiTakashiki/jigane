# jigane.reviews

レビューの組み立て、保存、読み込み、更新、選別、付属の文書を受け持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `lifecycle` | レビューの作成、更新、保存、読み込みを持ちます |
| `relations` | 同じ変更を重ねた別のレビューの探索を持ちます |
| `selection` | 指摘の選別（採用、見送り、保留）の記録を持ちます |
| `group_state` | 群ごとの実行の状態を持ちます |
| `copying` | レビューの置き場の複製を持ちます |
| `attachments` | レビューに付ける文書を持ちます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`findings`、`flaky`、`gates`、`git`、`groups`、`judgment_map`、`pull_request_context`、`storage`

読まれるサブパッケージ：`commands`、`design`、`policy_materials`、`publishing`、`sequence`、`skills`、`technology_list`

<!-- END GENERATED: dependencies -->
