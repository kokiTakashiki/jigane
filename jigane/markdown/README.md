# jigane.markdown

Markdown の柵と見出しの判別、本文を柵で囲む処理、表のセルの整形を受け持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `blocks` | コードブロックの柵と ATX 見出しを判別します。宣言の抜粋と設計書の読み取りが共有します |
| `fence` | 本文を、本文の中のバッククォートで閉じられない柵で囲みます。差分とファイルの全文と文書をプロンプトへ入れるときに使います |
| `cells` | 表のセルに入れる文字列を、区切りや改行を無害にして整えます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：なし

読まれるサブパッケージ：`commands`、`design`、`figures`、`judgment_map`、`policy_materials`、`repository_docs`、`skills`、`technology_list`

<!-- END GENERATED: dependencies -->
