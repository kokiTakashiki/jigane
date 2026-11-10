# jigane.text

外のコードやレビューの対象から来た文字列を、端末へ出せる形にします。配布名の正規化も持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `document_files` | 文書の拡張子（.md、.rst、.txt）と、変更したファイルが文書だけかの判定、文書だけの範囲を知らせる文を持ちます |
| `distribution_names` | `normalize_distribution` が配布名を PEP 503 の形に正規化します |
| `terminal` | `sanitize_text` が制御文字、双方向制御、端末のエスケープ列を除き、`one_line_text` がさらに空白の連なりを空白一つに畳みます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：なし

読まれるサブパッケージ：`commands`、`languages`、`policy_materials`、`reviews`、`skills`、`static_analysis`、`technology_list`

<!-- END GENERATED: dependencies -->
