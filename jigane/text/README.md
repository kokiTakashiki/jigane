# jigane.text

外のコードやレビューの対象から来た文字列を、端末へ出せる形にします。配布名の正規化と、外から来た JSON の入れ子の深さの制限も持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `document_files` | 文書の拡張子（.md、.rst、.txt）と、Markdown だけの判定（`is_markdown_path`）、変更したファイルが文書だけかの判定、文書だけの範囲を知らせる文を持ちます |
| `path_suffix` | `suffix_of` が、パスの `\` を `/` に直して拡張子を小文字で返します。言語の登録と解析しないファイルの分類、文書の判定が同じ取り出しを使います |
| `json_input` | `loads_bounded` が、文字列の外の `[` と `{` の深さを先に数え、上限を超えた JSON を `JsonTooDeepError` で拒んでから読みます。拒む深さが Python の版とスタックの大きさに左右されないようにします |
| `distribution_names` | `normalize_distribution` が配布名を PEP 503 の形に正規化します |
| `prompt_labels` | プロンプトの固定のラベルの文言と一致の方式（`PromptLabel`）を持ちます。雛形、組み立て、送る情報の対応表が同じ定義を読みます |
| `terminal` | `sanitize_text` が制御文字、双方向制御、端末のエスケープ列を除き、`one_line_text` がさらに空白の連なりを空白一つに畳みます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：なし

読まれるサブパッケージ：`commands`、`judgment_map`、`languages`、`llm_runs`、`policy_materials`、`pull_request_context`、`reviews`、`sequence`、`skills`、`static_analysis`、`technology_list`

<!-- END GENERATED: dependencies -->
