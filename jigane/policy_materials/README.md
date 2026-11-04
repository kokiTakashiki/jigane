# jigane.policy_materials

方針の群に渡す資料（図と表）を組みます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `design_doc` | 設計書の全文、構造、名指しの照合の表、選んだファイル、いまの形の図から、設計書の群の資料を組みます |
| `current_shape` | 保存済みのレビューを経ずに、いまのコードの形を図にします |
| `changed_files` | 変更したファイルの種別、ツリー図、判断の箇所の一覧から、方針に渡す資料の本体を組みます |
| `classes` | クラス図の材料を組みます |
| `type_deltas` | 変更した型と参照される型の、変更前後の差（追加、移動、項目の印、削除、基底の差、列挙の値）を作ります |
| `er` | ER 図の材料を組みます |
| `discriminators` | 判別の流れ図の材料を組みます |
| `declarations` | 箇所を、役割と持ち主を付けた宣言の並びへ展開します |
| `moved_declarations` | 別のファイルへ移しただけの宣言を見つけます |
| `value_sets` | 追跡されたファイルから値の組を集め、珍しい値（小さな木では少なくとも 5 ファイル未満に現れる値）で絞り、重なる組をまとめます。定義が無くても、同じファイルで同じ式を三つ以上の異なる値と等値で比べているだけの組は、定義の無い組として行にします（組の拾い方は言語アダプタの `ValueSetReader`）。まとめた行も、定義の場所ごとに名前に束ねた定義かリテラルかの区別を保ちます |
| `cochange` | git の履歴から、一緒に変わったファイルの対と強さを数えます |
| `missing` | 資料の材料の欠けの種類（`MissingMaterial`）と、その表示文を持ちます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`design`、`figures`、`git`、`groups`、`judgment_map`、`languages`、`markdown`、`preconditions`、`reviews`、`sequence`

読まれるサブパッケージ：`skills`

<!-- END GENERATED: dependencies -->
