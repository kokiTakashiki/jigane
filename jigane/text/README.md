# jigane.text

外のコードやレビューの対象から来た文字列を、端末へ出せる形にします。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `terminal` | `sanitize_text` が制御文字、双方向制御、端末のエスケープ列を除き、`one_line_text` がさらに空白の連なりを空白一つに畳みます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：なし

読まれるサブパッケージ：`commands`、`languages`、`static_analysis`

<!-- END GENERATED: dependencies -->
