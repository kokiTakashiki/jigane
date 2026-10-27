# jigane.llm_runs

LLM の起動、実行の記録、見積もりと使用量、使った額を受け持ちます。

## 主な入口

| モジュール | 役割 |
| --- | --- |
| `claude` | Claude Code CLI を起動します |
| `finding_model` | 指摘の pydantic のモデル。保存の前の検証が使い、旧の JSON スキーマと同じ入力を受け付けます |
| `output_common` | LLM の出力のモデルが共有する約束（余計な項目を拒む設定と整数の型）を持ちます |
| `launch_failure` | 起動の失敗の記録を持ちます |
| `prompt_stamp` | 起動したプロンプトの版と文字数の組（`PromptStamp`）を持ちます。`launch_failure` が失敗の記録に載せます |
| `defaults` | 1 回の起動の上限と effort の既定を決めます |
| `usage_table` | `usage.csv` の列と値の定義を持ちます |
| `claude_inputs` | jigane が Claude Code に渡す情報の種類（`ClaudeInput`）と、雛形の差し込み口からの対応表を持ちます |
| `cost` | 起動で使った額とその出所を持ちます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`findings`、`git`、`groups`、`languages`

読まれるサブパッケージ：`commands`、`skills`

<!-- END GENERATED: dependencies -->

## Q&A

### なぜ外部コマンドの起動を `git.process.run_command` と `claude.run` に限るのですか

外部コマンドの起動は `git.process.run_command` か `claude.run` を通し、それ以外の `subprocess` や `os.system` は ruff の `TID251`（banned-api）が禁じます。起動を窓口に集めると、`run_command` が git のときだけ親の `GIT_DIR` などを子の環境から外す処理と、検査での差し替えが、全ての起動に一か所で効くためです。
