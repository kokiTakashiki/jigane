# jigane.groups

群（方針、実装、セキュリティ）の識別子、表示名、既定を持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `definitions` | 群の表を持ちます。識別子、表示名、群ごとの起動の既定をここだけに置きます |
| `policy_runs` | 方針のレビューの実行の記録に書く形（`PolicyShape`）と役（`PolicyRole`）の閉じた集合と、使用量の表に出す役の名前を持ちます。見積もりの過去実額の基準は図の起動の行だけで、原則の行は数えません。廃止した一回の形と四回の形の値も、過去の記録を読むために持ちます |
| `viewpoints` | 観点の表（`viewpoints.csv`）を読み込む型と引く口を持ちます。観点ごとに変わらない uuid、表示の番号、名前、問いの文をここだけに置きます。設計の原則（P1〜P4、kind は `design_principle`）も同じ表に載りますが、群の列は空で、群に属しません。`design_principles()` が四つを表の順に返し、`of_group(MICRO)` は D と T の後にこの四つを足して返します。`of_group(MACRO)` と `of_group(SECURITY)` には入りません。コードが名指しする観点は、原則も含めて uuid の定数（`SINGLE_SOURCE`、`YAGNI`、`KISS`、`SEPARATION_OF_CONCERNS` など）で持ちます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：なし

読まれるサブパッケージ：`commands`、`design`、`findings`、`llm_runs`、`policy_materials`、`publishing`、`reviews`、`skills`

<!-- END GENERATED: dependencies -->
