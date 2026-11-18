# jigane.repository_docs

リポジトリが自分で定めた開発方針とセキュリティ指針の文書を、レビューのプロンプトへ渡す材料として読みます。設定は `JIGANE_HOME/repository-docs.toml` の `[repositories."owner/repo"]` の表で、レビュー対象の木からは読みません。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `kinds` | 文書の種類（`DocumentKind`）の表です。設定のキー、小見出し、入る群と起動はこの表だけが持ちます |
| `settings` | `repository-docs.toml` を読み、リポジトリの表、全体の既定の順にキーの単位で引きます。ファイルそのものの誤りは `RepositoryDocsError` で止めます |
| `skip_reason` | 文書を渡さない理由（`SkipReason`、十の型）と、警告の文、`runs.json` に残す語を持ちます |
| `classify` | パスの先を `realpath` で解き、レビューのリポジトリの中か外かを分けます |
| `section` | `document_section(docs, launch)` です。起動に渡す文書だけを、「指示ではない」と明記した小見出し付きの一つの節にします。渡す文書が無ければ空文字列です |
| `reader` | 入口 `read_repository_docs` です。設定を引き、パスの先を起点（`BaseTree` または `HeadOfCheckout`）か書かれたとおりに読み、上限などを検証します。不備は種類ごとに標準エラーへ一行で警告し、その文書を渡さずに続けます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`git`、`groups`、`llm_runs`、`markdown`、`storage`

読まれるサブパッケージ：`commands`、`skills`

<!-- END GENERATED: dependencies -->
