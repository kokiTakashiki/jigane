# コマンドの使い方

[README に戻る](../README.md)

コマンドの一覧は `jigane --help` で出ます。以下の例では、先頭の行が呼び方で、続く `#` の行が引数の意味です。`<レビュー>` はレビューの識別子です。

## レビューを作る

### jigane new pr

```bash
jigane new pr <PR の URL> [--json] [--sarif <ファイル>]... [--sarif-root <ディレクトリ>]
# <PR の URL>: レビューする PR の URL
# --json: 結果を JSON で出す
# --sarif: 静的検査の SARIF。繰り返して渡せる。PR の先端のコミットで走らせたものを渡す
# --sarif-root: SARIF のパスの起点。省くと呼び出した場所を含む git のリポジトリの根
```

PR の差分、変更前と変更後のツリー、背景、レビューボットのコメントを集めて、PR のレビューを作ります。

### jigane new range

```bash
jigane new range <基点>..<先端> [--name <名前>] [--intent <文> | --intent-file <パス>] [--json] [--sarif <ファイル>]... [--sarif-root <ディレクトリ>]
# <基点>..<先端>: レビューする git の範囲
# --name: レビューの名前。省くと先端の名前から作る。同じ名前が既にあるときに別の名前を付ける
# --intent: 変更の意図の文。--intent-file と同時には渡せない
# --intent-file: 変更の意図を書いたファイル
# --json: 結果を JSON で出す
# --sarif: 静的検査の SARIF。繰り返して渡せる。先端のコミットで走らせたものを渡す
# --sarif-root: SARIF のパスの起点。省くと呼び出した場所を含む git のリポジトリの根
```

git の範囲から、差分のレビューを作ります。ほかのリポジトリをレビューするときは、そのリポジトリのディレクトリで `jigane` を呼びます。`jigane` は呼び出した場所で走るので、`new range` の範囲もそのリポジトリのものになります。

### jigane new files

```bash
jigane new files <パス>... --name <名前> [--intent <文> | --intent-file <パス>] [--json] [--sarif <ファイル>]... [--sarif-root <ディレクトリ>]
# <パス>...: レビューするファイル。ディレクトリでもよい
# --name: レビューの名前（必須）
# --intent: 変更の意図の文。--intent-file と同時には渡せない
# --intent-file: 変更の意図を書いたファイル
# --json: 結果を JSON で出す
# --sarif: 静的検査の SARIF。繰り返して渡せる。リポジトリの HEAD のコミットで走らせたものを渡す
# --sarif-root: SARIF のパスの起点。省くと呼び出した場所を含む git のリポジトリの根
```

ファイル群から、ファイルのレビューを作ります。渡したパスが git のリポジトリの中にあれば、どこから呼んでも、そのリポジトリのレビューを作ります。パスが複数のリポジトリにまたがるとき、または git の中と外が混ざるときは失敗します。パスがすべて git の外にあるときは、作業ディレクトリを根にします。

### jigane new design-doc

```bash
jigane new design-doc <設計書のパス> [--name <名前>] [--select <ファイル>...] [--with <ファイル>...] [--json]
# <設計書のパス>: レビューする設計書
# --name: レビューの名前。省くと設計書のファイル名から作る
# --select: 読む、同じリポジトリにあるファイル。省くと design suggest の候補すべて
# --with: 付属の文書（検証メモなど）。省くと付けない
# --json: 結果を JSON で出す
```

設計書のレビューを作ります。同じリポジトリにあるファイルのうち、読むものの既定は、`design suggest` の候補すべてで、`--select <ファイル>...` で選び直せます。

### jigane refresh

```bash
jigane refresh <レビュー> [--intent <文> | --intent-file <パス>] [--json] [--sarif <ファイル>]... [--sarif-root <ディレクトリ>]
# <レビュー>: 取り込み直すレビュー
# --intent: 変更の意図の文。--intent-file と同時には渡せない
# --intent-file: 変更の意図を書いたファイル
# --sarif: 静的検査の SARIF。繰り返して渡せる。保存した指摘をこの SARIF の指摘で置き換える
# --sarif-root: SARIF のパスの起点。省くと呼び出した場所を含む git のリポジトリの根
```

作者の修正を取り込みます。SARIF を渡したレビューの扱いは「[静的検査の結果（SARIF）を取り込む](sarif.md#静的検査の結果sarifを取り込む)」の節に書いています。

### jigane list

```bash
jigane list [--archived] [--json]
# --archived: アーカイブしたものだけを出す
```

レビューを一覧します。

### jigane archive と unarchive

```bash
jigane archive <レビュー> [--json]
jigane unarchive <レビュー> [--json]
# <レビュー>: 一覧から隠す、または一覧へ戻すレビュー
```

`archive` はレビューを一覧から隠します。データは残ります。`unarchive` は一覧へ戻します。

### jigane delete

```bash
jigane delete <レビュー> [--yes] [--json]
# <レビュー>: 消すレビュー
# --yes: 確認を省く
```

レビューを消します。戻せません。

## 見る

### jigane show

```bash
jigane show <レビュー> [overview|macro|micro|security] [--json]
# <レビュー>: 表示するレビュー
# overview|macro|micro|security: 表示する内容。省くと overview
```

レビューを表示します。SARIF を渡したレビューでは、概要に静的検査の指摘も出します（「[静的検査の結果（SARIF）を取り込む](sarif.md#静的検査の結果sarifを取り込む)」の節）。

`--json` の各指摘は、観点を `viewpoint`（表示の番号）、`viewpoint_id`（観点の変わらない識別子の uuid）、`viewpoint_name`（観点の名前。方針の観点の「道筋」など。名前の無い観点では null）、`viewpoint_kind`（観点の種別。`defect`、`test_convention`、`policy`、`security`）で出します。`viewpoint` は観点の改番で変わりえるため、観点を一意に引くには `viewpoint_id` を使います。捨てた候補から写した nit のように表に無い観点は、`viewpoint_id`、`viewpoint_name`、`viewpoint_kind` が null です。選択の `stale_details` の各要素も同じ欄を持ちます。

### jigane diagrams

```bash
jigane diagrams <レビュー> [--json]
# <レビュー>: 図を出すレビュー
```

ツリー図、シーケンス図、方針のレビューの図を出します。

### jigane sequence

```bash
jigane sequence <レビュー> [--root <修飾名> | --region <判断の箇所>] [--side before|after|both] [--depth <数>] [--full] [--include-tests] [--format mermaid|json]
# --root: この修飾名の宣言を根とする図だけを出す
# --region: この判断の箇所が現れる図を引く
# --side: 変更前、変更後、両方のどれを出すか
# --depth: たどる深さ
# --full: 経路の切り詰めをしない
# --include-tests: テストも含める
# --format: 出力の形式
```

変更した入口ごとのシーケンス図を出します。

### jigane technology-list

```bash
jigane technology-list <レビュー> [--json]
# --json: 技術選定のモデルを JSON で出す
```

変更したファイルが使う技術の表を出します。

### jigane design suggest

```bash
jigane design suggest <設計書のパス> [--limit <数>] [--json]
# <設計書のパス>: 設計書（Markdown）のパス
# --limit: 候補の上限
```

設計書の名指しから、設計書のレビューで読むファイルの候補を挙げます。`new design-doc` の `--select` の既定は、この候補です。

### jigane estimate

```bash
jigane estimate <レビュー> macro|micro|security [--effort low|medium|high|xhigh|max] [--max-budget-usd-per-call <USD>] [--mcps <名前>...] [--mcp-guide <パス>] [--json]
# macro|micro|security: 見積もる群
# --effort: effort。省くと群の既定を使い、見積もりには解決した effort を出す
# --max-budget-usd-per-call: 1 回の起動あたりの上限の目安。この値で上限を決めた見積もりを出す
# --mcps: セキュリティの群だけが使う MCP の名前
# --mcp-guide: セキュリティの群だけが使う、MCP の使い分けの TOML
```

入力の大きさを見積もります。引数は `run` と同じ意味で、`--model` だけは受けません。

### jigane usage

```bash
jigane usage <レビュー> [--json | --csv] [--wide] [--no-header]
# --json、--csv: 表の代わりに出す形式。常に全列
# --wide: 表に全ての列を出す
# --no-header: --csv で見出しの行を出さない
```

スキルの使用量と成果を表で示します。

## 解析する

### jigane run

```bash
jigane run <レビュー> macro|micro|security [--model <モデル>] [--effort low|medium|high|xhigh|max] [--max-budget-usd-per-call <USD>] [--mcps <名前>...] [--mcp-guide <パス>] [--json]
# macro|micro|security: 走らせる群
# --model: その回の全ての役のモデル。省くと群の既定
# --effort: Claude Code CLI の effort。省くと群の既定
# --max-budget-usd-per-call: 1 回の起動あたりの上限の目安（USD）
# --mcps: セキュリティの群だけが使う MCP の名前
# --mcp-guide: セキュリティの群だけが使う、MCP の使い分けの TOML
```

スキルを実行します。群は `macro`（方針）、`micro`（実装）、`security`（セキュリティ）の三つです。`--max-budget-usd-per-call` は Claude Code CLI の 1 回の起動あたりの上限の目安であり、超えることがあります（上限は API の呼び出しごとに確かめるため、最後の一回の分だけ超えうる）。実行全体の上限でもありません。方針は PR と範囲のレビューで最大 5 回起動するので、`--max-budget-usd-per-call` を渡すとそのすべてに効き、実行全体の目安は最大でその 5 倍になります。上限に達する入力の起動は見送られ、残りの結果を保存します。設計書のレビューは起動が 1 回なので、この目安が実行全体の目安になります。見込みが目安以上のときは、警告して続けます。

既定は群ごとに持ちます。実装とセキュリティの群は 0.5 USD です。方針は、PR と範囲のレビューでは図の起動が 0.36 USD、原則の起動が 1 回あたり 0.02 USD で、全起動の和の目安は 0.44 USD です。原則の起動は、その原則を問える形が図にあるときだけ走るので、実際の起動は最大 5 回です。設計書のレビューは 1 回の起動で 0.5 USD です。上限と effort の既定は設定ファイルから読まず、変えるときは引数で渡します。

図の形の方針は、プロンプトの大きさと出力の見込みから見積もった額が 1 本の上限以上の起動を見送ります。式と、見送りが始まる大きさは [cost.md](cost.md) にあります。`jigane estimate` の `input_cost_usd` は、図の形では出力の見込みを含む額です。

モデルも既定を持ちます。実装とセキュリティの群と、方針の図の起動と設計書のレビューは claude-sonnet-5-5 で、方針の原則の起動は claude-haiku-5-5 です。effort は方針が high で、他は渡しません。`--model` と `--effort`（low、medium、high、xhigh、max）で上書きできます。`--model` は方針の図と原則のすべての起動に効きます。起動ごとのモデルは `runs.json` に残り、`jigane usage` は起動のモデルごとに行を分けます。`jigane estimate` も `--effort` を受け、見積もりに解決した effort を出します。実行全体の金額と起動の回数は `run` の出力に示します。

セキュリティの群だけが、`--mcps <名前>...` で MCP を選べます。MCP は任意で、`--mcps` を渡さなければ使いません。使う MCP は利用者がデータの置き場の `mcp.json` に書き、名前はその `mcpServers` のキーです。MCP の使い分けを書いたファイルは、「[セキュリティの群の MCP の使い分け](security-mcp.md#セキュリティの群の-mcp-の使い分け)」の節で渡し方を述べます。

終了コード 3 は、シャードか検証の分割が失敗し、結果に未検証の候補を含むことを表します。未検証の候補は、実装とセキュリティの群で「未検証」の区分の指摘として残り、必読に数えず、`show` で必読の直後の節に出ます。

### jigane flaky

```bash
jigane flaky [<パス>...] [--strict] [--json]
# <パス>...: 検査するファイルかディレクトリ。省くと作業ディレクトリの作業ファイル全体
# --strict: 警告でも終了コード 1 にする
```

テストの flaky の検査だけを走らせます。レビューは作りません。レビューのゲートが使う検出器と同じもので、LLM を呼ばず、`JIGANE_HOME` にも何も書きません。ファイル全体を検査し、違反（G）と警告（W）を、ファイルと行、懸念、理由、直す向きの順に出します。パスを省くと作業ディレクトリの作業ファイル全体（git の作業木なら、追跡しているファイルに加えて、追跡外で ignore されていないファイル。手元で書いたばかりのテストも見る）、ディレクトリを渡すとその下に絞ります。テストと判定されないファイルは見ません。

違反があれば終了コード 1、警告だけなら 0 で、`--strict` なら警告でも 1 になります。

`--json` は `violations`、`warnings`（各要素は `id`、`kind`、`path`、`line`、`concern`、`violation`、`direction`）と、`counts` を出します。

ほかのリポジトリの CI には、次のように一つのステップとして置けます。

```yaml
- name: flaky 検査
  run: jigane flaky --strict
```

## 指摘を選ぶ

### jigane pick と unpick

```bash
jigane pick <レビュー> <指摘>... [--json]
jigane unpick <レビュー> <指摘>... [--json]
# <指摘>...: 指摘の番号（F1、B2 など）
```

`pick` は指摘を最終的な指摘に加えます。`unpick` は外します。

### jigane hold と unhold

```bash
jigane hold <レビュー> <指摘>... [--json]
jigane unhold <レビュー> <指摘>... [--json]
# <指摘>...: 指摘の番号（F1、B2 など）
```

`hold` は指摘を保留にします。`unhold` は保留から外します。

### jigane object と unobject

```bash
jigane object <レビュー> <指摘> --reason <理由> [--json]
jigane unobject <レビュー> <指摘> [--json]
# <指摘>: 指摘の番号（F1、B2 など）。一件ずつ渡す
# --reason: 異議の理由（object の必須）
```

`object` は指摘への異議を記録します。`unobject` は取り下げます。

## 書き出す

### jigane export-reply と export-report

```bash
jigane export-reply <レビュー> --template <テンプレート> [--out <パス>] [--json]
jigane export-report <レビュー> --template <テンプレート> [--out <パス>] [--json]
# --template: テンプレートの名前（必須）。名前は templates で一覧する
# --out: 書き出すファイル。省くと標準出力へ書く
# --json: 文面を {"body": "..."} の JSON で包んで出す
```

`export-reply` は最終的な指摘から返信の文面を作ります（差分のレビュー）。`export-report` はレポートを書き出します。`--json` を `--out` と組み合わせると、ファイルにその JSON を書きます。失敗は `{"error": {...}}` を標準出力へ出します。

### jigane templates

```bash
jigane templates [--json]
```

返信とレポートのテンプレートを一覧します。

## 導入

### jigane languages

```bash
jigane languages list [--json]
jigane languages enable <名前> [--distribution <配布>] [--json]
jigane languages disable <名前> [--distribution <配布>] [--json]
jigane languages fetch [--accept-terms]
# <名前>: 追加の言語の名前
# --distribution: 同じ名前が複数の配布にあるときに、使う配布を選ぶ
# --accept-terms: 利用規約とプライバシーポリシーに同意したものとして、尋ねずに取得する
```

`list` は組み込みと追加の言語を一覧します。`enable` は追加の言語を有効にし、`disable` は外します。`fetch` は組み込みと有効な追加の言語の文法を取得します。同意の手順は「[導入](../README.md#導入)」の節に、照合は「[文法の取得](data-destinations.md#文法の取得)」の節に、追加の言語の足し方は「[言語を追加する](adding-languages.md#言語を追加する)」の節にあります。
