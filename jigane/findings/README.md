# jigane.findings

指摘の保存と選別の記録、捨てた理由を受け持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `records` | スキルの結果（`findings.json`）の読み書きと、古い印を持ちます。群の結果を差し替えるとき、差し替えられる指摘を回ごとに `findings_history.json` へ残します |
| `discarded_nits` | 方針の群が捨てた候補を nit の指摘に写します。新しい記録は実行のときに写し、古い記録は読むときに同じ写し方で組み立てます |
| `discard` | 方針のレビューが捨てた候補と、捨てた割合の三状態を持ちます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`groups`、`storage`

読まれるサブパッケージ：`commands`、`llm_runs`、`publishing`、`reviews`、`skills`

<!-- END GENERATED: dependencies -->
