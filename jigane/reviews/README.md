# jigane.reviews

レビューの組み立て、保存、読み込み、更新、選別、付属の文書を受け持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `lifecycle` | レビューの作成、更新、保存を持ちます |
| `resolution` | 識別子の入力（完全な形、短い形、名前だけ）を保存済みのレビューの一つに決めます。catalog の上に載ります |
| `catalog` | 保存済みのレビューの読み出し、一覧、アーカイブの印を持ちます。一覧は要約の行だけを軽く読む入口（`list_review_summaries`）で引きます。要約の読みは完全な読みより検査が緩く、`map.json` の本体と背景、ゲートの形は検査しません（一覧が引く項目だけを検査する意図の差です）。読めない記録の警告は 1 件ずつ出ますが、`collapse_skipped_warnings` の間は溜めて、同じ記録を一度だけ数え、抜けるときに件数と `jigane list` の案内を 1 行で出します（CLI の入口 `jigane.commands.main` が、`list` を除く全副コマンドを囲み、抜ける前に標準出力を書き出します） |
| `kind_fields` | 種類から導く JSON の項目（kind、mode、is_diff、is_pull_request）の定義を持ちます。lifecycle と catalog が参照します |
| `review_formats` | review.json、regions.json、pairs.json の版と、読み書きの形を持ちます。古い版は読まずに止めます |
| `stored_pairs` | 保存した pairs.json を読み、変更したファイルの対を返します |
| `relations` | 同じ変更を重ねた別のレビューの探索を持ちます |
| `selection` | 指摘の選別（採用、見送り、保留）の記録を持ちます |
| `group_state` | 群ごとの実行の状態を持ちます |
| `copying` | レビューの置き場の複製を持ちます |
| `attachments` | レビューに付ける文書を持ちます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`findings`、`flaky`、`gates`、`git`、`groups`、`judgment_map`、`pull_request_context`、`static_analysis`、`storage`、`text`

読まれるサブパッケージ：`commands`、`design`、`policy_materials`、`publishing`、`sequence`、`skills`、`technology_list`

<!-- END GENERATED: dependencies -->
