# jigane.gates

ゲートの違反と警告の一件の型、その説明の文、検出の対象の行の範囲を持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `entry` | `gates.json` の違反と警告の一件の型です。強さと出所を一か所で持ち、`gates.json`（形の版 3）の形と往復します。警告の出所は flaky と、読めない記述（`unreadable`）です |
| `tree_side` | 木の側（変更前か変更後）の二値の型です |
| `explanation` | ゲート違反を、規則の ID の代わりに読ませる三つの文の型です |
| `line_scope` | 検出が対象にする行の範囲（ファイル全体か、パスごとに変えた行か）を表します |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：なし

読まれるサブパッケージ：`commands`、`flaky`、`judgment_map`、`publishing`、`regex`、`reviews`、`sequence`

<!-- END GENERATED: dependencies -->

- 返信の経路を共通にするために共有します。
