# jigane.groups

群（方針、実装、セキュリティ）の識別子、表示名、既定を持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `definitions` | 群の表を持ちます。識別子、表示名、群ごとの起動の既定をここだけに置きます |
| `policy_runs` | 方針のレビューの実行の記録に書く形（`PolicyShape`）と役（`PolicyRole`）の閉じた集合と、使用量の表に出す役の名前を持ちます。廃止した一回の形と四回の形の値も、過去の記録を読むために持ちます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：なし

読まれるサブパッケージ：`commands`、`design`、`findings`、`llm_runs`、`policy_materials`、`publishing`、`reviews`、`skills`

<!-- END GENERATED: dependencies -->
