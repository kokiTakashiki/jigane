# jigane.commands

副コマンドの入口と、表示の行の組み立てを受け持ちます。引数を解釈して副コマンドを選び、`jigane` の他のモジュールを呼んで結果を標準出力へ書き、失敗を終了コードと標準エラーに変えます。レビューの判断や保存の規則は持たず、呼び出しと表示に徹します。

## 主な入口

| 名前 | 役割 |
| --- | --- |
| `commands.main` | 解析、実行、失敗の表示を行い、終了コードを返す唯一の入口です |
| `commands.build_parser` | 副コマンドを登録します（`subcommands/` の各モジュールの `register` を呼びます） |
| `subcommands/<名前>.py` | 副コマンド 1 つにつき 1 ファイルです（`register` と `run` を持ちます） |
| `context.Context` | 実行の関数へ渡す依存（Claude Code CLI を起動する関数）を持ちます |
| `output` | 標準出力へ書く部品（行、JSON、最終的な指摘）です |
| `views/` | 表示の行を組み立てる部品です（標準出力へは書きません） |
| `failures.describe` | 例外を、種類、見出し、理由、次の操作へ変換します |
| `exit_codes` | 終了コードの定数を持ちます |
| `budget_argument.add_max_budget_argument` | `--max-budget-usd-per-call` の引数を定義します。`run` と `estimate` が共有します |
| `group_argument` | 引数の群を入口で一度だけ `GroupId` に解き、群を取る引数を足します |
| `mcp_argument` | `--mcps` の引数の定義と検証をします。`run` と `estimate` が共有します |
| `consent` | `jigane run` が Claude Code CLI を起動する前に、利用者の同意を取ります |
| `terms_consent` | `jigane languages fetch` が文法を取得する前に、利用規約への同意を取ります |
| `public_urls` | 同意の画面が示す規約、プライバシー、SECURITY.md の URL を、入れた版に合わせて作ります |
| `run_guide.run_guide` | 見積もりの JSON の `run_guide`（未実行の群の案内の文）を、計画の種類と群ごとに返します |
| `generation.generate` | 返信とレポートの文面を作る共通の手順です |

## 副コマンド

- レビューの作成と更新：`new`、`refresh`（出所の型から更新の手への振り分け `refresh_review` を持ち、種類ごとの更新は `reviews.lifecycle` と `design.review_documents` が持ちます）、`delete`、`archive`、`unarchive`
- 表示：`list`、`show`、`diagrams`、`sequence`、`usage`、`estimate`
- 設計書の `new design-doc`：設計書をレビューします。実装の前に方針の綻びを問います。出所は設計書のパスと版、コードの版、選んだファイル、`--with` で選んだ付属の文書のパスと版です。
- 設計書の `design suggest`：設計書の名指しから、突き合わせるファイルと、付属の文書の候補（同じ場所の reports/ の .md）を示します。保存はしません。
- スキルの実行：`run`
- 検査と一覧：`flaky`（テストの flaky の検査だけを走らせます）、`technology-list`（変更したファイルが使う技術の表を出します）、`languages`（言語の一覧、有効化、無効化、文法の取得）
- 最終的な指摘の編集：`pick`、`unpick`、`hold`、`unhold`、`object`、`unobject`
- 出力：`export-reply`、`export-report`、`templates`

副コマンドの一覧は `subcommands/` のモジュールそのものであり、別に対応表を持ちません。

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`design`、`figures`、`findings`、`flaky`、`gates`、`git`、`groups`、`judgment_map`、`languages`、`llm_runs`、`publishing`、`pull_request_context`、`reviews`、`sequence`、`skills`、`storage`、`technology_list`

読まれるサブパッケージ：なし

<!-- END GENERATED: dependencies -->

- `jigane.reviews.lifecycle` でレビューを読み込み、`jigane.design.review_documents` で設計書のレビューを作成と更新し、`jigane.skills` の `run` で群を走らせます。
- `jigane.__main__` が `commands.main` を呼びます。

副コマンドの引数、機械が読む出力（`--json`）、失敗の JSON の鍵（`failures` の `Failure.to_dict`）、終了コード（`exit_codes`）は、外の道具との契約です。機械が読む出力を出す副コマンドは、すべて `--json` を持ちます。失敗は `--json` を付けたとき（`sequence` は `--format json` を付けたとき）だけ、標準出力に `{"error": {...}}` で出ます。`sequence` は `--json` を持たず、出力の形を `--format` で選びます。例外は、人向けの進捗だけを出す `languages fetch` です。

## Q&A

### なぜ副コマンドを足すときは `subcommands/` にモジュールを足すのですか

副コマンドを足すときは `subcommands/` にモジュールを足します。`register` を持たないモジュールがあると、パーサの組み立てで失敗します。副コマンドの一覧は `subcommands/` のモジュールそのものであり、ファイルを足せばコマンドが足される形にして、副コマンドを足すたびに巨大な分岐と対応表を直す必要をなくしたためです。

### なぜ新しい例外の型を足すときは `failures.KINDS` にも行を足すのですか

新しい例外の型を足すときは `failures.KINDS` にも行を足します。足し忘れた型は `unexpected` として表示されます。`KINDS` に当たらない型は、型ごとの見出しと次の操作を持たない `unexpected` の種類に落ちるためです。

### なぜ Namespace を受けるのは各副コマンドの run と最上位の受け口だけなのですか

Namespace を受けるのは各副コマンドの run と最上位の受け口だけで、ほかの関数には名前と型の付いた値で渡します。共有の関数が Namespace をまるごと受けると、どの属性を読むかがシグネチャに現れず、副コマンドの引数を変えても型検査が食い違いを見つけないためです。

### なぜ `views/` は行を返すだけで、標準出力へ書くのは `output` と `subcommands/` なのですか

`views/` は行を返すだけで、標準出力へ書くのは `output` と `subcommands/` です。文字と JSON のどちらで出すかを出口の一か所で決めるためと、試験が標準出力を捕まえずに行を比べられるようにするためです。
