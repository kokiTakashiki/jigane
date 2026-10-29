# jigane.git

外部コマンドの起動と、git の木の読み出しを受け持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `process` | 外部コマンドを起動します（子の git に向き先の変数を渡しません） |
| `environment` | 子の git から外す向き先の変数の一覧です（標準ライブラリだけで動きます） |
| `repository` | 作業コピーの根と、走査から外すディレクトリ名を持ちます |
| `tree_files` | 木のファイルの列挙と読み取りの入口です |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：なし

読まれるサブパッケージ：`commands`、`design`、`flaky`、`judgment_map`、`llm_runs`、`policy_materials`、`pull_request_context`、`regex`、`reviews`、`sequence`、`skills`、`static_analysis`、`technology_list`

<!-- END GENERATED: dependencies -->
