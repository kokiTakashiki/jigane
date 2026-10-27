# jigane.pull_request_context

PR の取得、変更の背景、レビューボットの指摘を受け持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `fetching` | PR の取得、キャッシュ、ツリーの展開、変更ファイルの対を持ちます |
| `background` | 参照文書、過去の PR、所有者の収集を持ちます |
| `bots` | レビューボットのコメントと、指摘への対応の判定を持ちます |
| `gh` | GitHub CLI（`gh`）の呼び出しの唯一の入口です。起動してよい三つの形を `ALLOWED_SHAPES` に限り、argv を組んだ後、起動する前に照合します |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`git`、`judgment_map`、`preconditions`、`storage`

読まれるサブパッケージ：`commands`、`reviews`、`skills`

<!-- END GENERATED: dependencies -->
