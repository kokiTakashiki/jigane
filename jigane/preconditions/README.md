# jigane.preconditions

呼び出し側が保証している前提を確かめ、型を絞ります。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `integers` | 外から読んだ値が整数かを確かめ、`bool` を除いて `int` に絞ります。`non_negative_int` は 0 以上、`positive_int` は 1 以上を通し、外れたら `None` を返します |
| `required` | `None` でないと呼び出し側が保証している値を取り出し、型を絞ります。`None` なら `AssertionError` を投げます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：なし

読まれるサブパッケージ：`flaky`、`judgment_map`、`languages`、`policy_materials`、`pull_request_context`、`sequence`、`skills`

<!-- END GENERATED: dependencies -->
