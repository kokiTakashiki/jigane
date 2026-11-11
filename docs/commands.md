# コマンドの使い方

[README に戻る](../README.md)

コマンドの一覧は `jigane --help` で出ます。以下の例では、先頭の行が呼び方で、続く `#` の行が引数の意味です。`<レビュー>` はレビューの識別子です。

## 解析しない言語の案内

範囲のファイルに、解析しない言語のソースが含まれるときは、`new`、`estimate`、`run` が案内を出します。読めない記述は「[読めない記述の案内](#読めない記述の案内)」の節にあります。数えるのは判断の地図の区間のファイルで、文書とデータの形式（Markdown、JSON など）、拡張子の無いファイル、ビルドのファイルは数えません。設計書のレビューでは出ません。案内は二つに分かれます。

| 分け | 案内 |
|---|---|
| 文法を取得していない言語（組み込みか有効にした追加の言語で、文法だけが無い） | 言語の名前と件数とファイルを示し、`jigane languages fetch <言語>` での取得を案内します |
| アダプタの無い言語（どの言語の拡張子でもない） | 拡張子ごとに件数とファイルを示し、[adding-languages.md](adding-languages.md) に従ったアダプタの追加か、`jigane languages enable` でのプラグインの有効化を案内します |

案内は標準エラーに出ます。`new` はレビューを作った後に、`estimate` は見積もりを出す前に出し、どちらも止めず、尋ねません。`--json` を付けても標準出力の結果と終了コードは変わりません。`run` は費用の同意の後に同じ案内を出し、続けるかを尋ねます。範囲に解析しない言語のファイルが無いときは、何も出ません。

## 読めない記述の案内

言語仕様の上で書ける記述でも、jigane が読めないものはサポートしません。判断の地図の区間のファイルにそのような記述があるときは、「解析しない言語の案内」の後に続けて案内を出します。出す経路は同じで、`new`、`estimate`、`run` の標準エラーです。設計書のレビュー、生成物の区間、ビルドのファイル、アダプタの無いファイルは対象外です。テストのファイルは対象です。

| 規則 | 対象の言語 | 当たる記述 |
|---|---|---|
| 構文エラー | アダプタのあるすべての言語 | 構文木のルートに誤りがある。最初の誤りの行を示します |
| 名前を取れない演算子関数 | Swift | `static func +` のように、関数の名前が識別子でない |
| 名前を取れない宣言子 | C# | クラス直下の `Foo.Bar(x);` のように、名前を持たない宣言子 |

構文エラーのファイルには、後ろの二つの規則を当てません。案内には、規則ごとの件数と最初の五つの `経路:行` を示します。変更前の木の記述には「変更前」が付きます。案内の末尾には、当たった種類ごとに直し方を一度だけ示します（警告の説明も同じ文です）。同じ検出を、レビューの結果に警告として保存します。`jigane show` の警告の列と、`--json` の `gates.warnings` に、`UNREADABLE-SYNTAX-ERROR`、`UNREADABLE-SWIFT-OPERATOR-FUNCTION`、`UNREADABLE-CSHARP-NAMELESS-DECLARATOR` の項目が並びます。`source` は `unreadable` で、項目には記述のある側（`side`: `before` か `after`）と該当行の本文が載ります。警告は変更した行に絞らず、`new` と `refresh` の時点の木から作ります。終了コードは変わらず、警告は数えません。新しいオプションはありません。`run` は、解析しない言語の案内と合わせて、続けるかを一度だけ尋ねます。

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

解析しない言語のソースのファイルが範囲にあるときは、「[解析しない言語の案内](#解析しない言語の案内)」と、読めない記述があるときは「[読めない記述の案内](#読めない記述の案内)」を出します。

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

解析しない言語のソースのファイルが範囲にあるときは、「[解析しない言語の案内](#解析しない言語の案内)」と、読めない記述があるときは「[読めない記述の案内](#読めない記述の案内)」を出します。

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

解析しない言語のソースのファイルが範囲にあるときは、「[解析しない言語の案内](#解析しない言語の案内)」と、読めない記述があるときは「[読めない記述の案内](#読めない記述の案内)」を出します。

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

作者の修正を取り込みます。範囲のレビューは、作ったときの作業ディレクトリがあり、先端を枝の名前や HEAD で書いていれば、範囲の文字列を解き直して先端を進めます。SHA やタグで作った範囲は、作ったときに先端だった枝（作業ディレクトリの今の枝の先端と一致したとき）が記録されていれば、その枝の今の先端へ進みます。作業ディレクトリが消えていても、記録した共通の git ディレクトリで回を作れます。記録した枝が無ければ記録した SHA のままにして、先端を進めなかったことと作り直しを案内します。進めた先が範囲の基点の子孫でなければ、基点を失ったので作り直すよう案内して止まります。結果には、回を作った先端の枝の名前と SHA、その先端を選んだ理由（`resolved_range`、`followed_branch`、`branch_missing`、`untracked`）を出します。`--json` では `head` に `branch`、`sha`、`reason` を持ちます。SARIF を渡したレビューの扱いは「[静的検査の結果（SARIF）を取り込む](sarif.md#静的検査の結果sarifを取り込む)」の節に書いています。

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

レビューを表示します。群を付けない表示（overview）の末尾には、指摘のある群ごとに、その群の指摘を一件ずつ見る `jigane show <レビュー> <群>` と指摘の件数を並べます。指摘が 0 件の群と未実行の群は並べず、どの群にも指摘が無いときは何も足しません。`--json` の概要には欄を足しません。SARIF を渡したレビューでは、概要に静的検査の指摘も出します（「[静的検査の結果（SARIF）を取り込む](sarif.md#静的検査の結果sarifを取り込む)」の節）。

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

`--json` の `expected` には、群の見込みを `total_usd`（発見役の見積もり × 検証役の倍率）、`version`、`discovery_estimate_usd`、`verifier_multiplier`、`verifier_multiplier_source` で出します。方針は検証役を持たないので倍率は 1 で、出所は `no_verifier` です。実装とセキュリティの倍率は、レビューの置き場の `runs.json` にある同じリポジトリの回の記録から引きます（`run_records`）。`jigane run` は回の記録へ、起動の前の発見役の見積もり（`discovery_estimate_usd`）と見積もりの数え方の版（`estimate_version`）を足します。倍率は、今の版の欄を持つ回について、群の実額を発見役の見積もりで割って平均した値です。欄の無い古い回と版の違う回は数えません。数える回が無ければ既定の 2.5 です（`default`）。`jigane usage` の出力には列を足しません。

解析しない言語のソースのファイルが範囲にあるときは、「[解析しない言語の案内](#解析しない言語の案内)」と、読めない記述があるときは「[読めない記述の案内](#読めない記述の案内)」を出します。

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

実行の前に費用の同意を尋ねます。問いには「発見役:」の行に続けて、「群の見込み: …」の行で発見役の見積もりと検証役の倍率、その積を示します。

スキルを実行します。群は `macro`（方針）、`micro`（実装）、`security`（セキュリティ）の三つです。`--max-budget-usd-per-call` は Claude Code CLI の 1 回の起動あたりの上限の目安であり、超えることがあります（上限は API の呼び出しごとに確かめるため、最後の一回の分だけ超えうる）。実行全体の上限でもありません。方針は PR と範囲のレビューで最大 5 回起動するので、`--max-budget-usd-per-call` を渡すとそのすべてに効き、実行全体の目安は最大でその 5 倍になります。上限に達する入力の起動は見送られ、残りの結果を保存します。ただし方針で図の起動が走らなかった回は、前の回の結果を残します（[reviews.md](reviews.md) の方針の節）。見送った起動は、`jigane run` の表示と `jigane show` の表示に「起動せず」の行で出ます。設計書のレビューは起動が 1 回なので、この目安が実行全体の目安になります。見込みが目安以上のときは、警告して続けます。

既定は群ごとに持ちます。実装とセキュリティの群は 0.5 USD です。方針は、PR と範囲のレビューでは図の起動が 0.36 USD、原則の起動が 1 回あたり 0.04 USD で、全起動の和の目安は 0.52 USD です。原則の起動は、その原則を問える形が図にあるときだけ走るので、実際の起動は最大 5 回です。設計書のレビューは 1 回の起動で 0.5 USD です。上限と effort の既定は設定ファイルから読まず、変えるときは引数で渡します。

方針の図の形と設計書の形は、プロンプトの大きさと出力の見込みから見積もった額が 1 本の上限以上の起動を見送ります。式と、見送りが始まる大きさは [cost.md](cost.md) にあります。`jigane estimate` の `input_cost_usd` は、図の形と設計書の形では出力の見込みを含む額です。

モデルも既定を持ちます。実装とセキュリティの群と、方針の図の起動と設計書のレビューは claude-sonnet-5-5 で、方針の原則の起動は claude-haiku-5-5 です。effort は方針が high で、他は渡しません。`--model` と `--effort`（low、medium、high、xhigh、max）で上書きできます。`--model` は方針の図と原則のすべての起動に効きます。起動ごとのモデルは `runs.json` に残り、`jigane usage` は起動のモデルごとに行を分けます。`jigane estimate` も `--effort` を受け、見積もりに解決した effort を出します。実行全体の金額と起動の回数は `run` の出力に示します。

セキュリティの群だけが、`--mcps <名前>...` で MCP を選べます。MCP は任意で、`--mcps` を渡さなければ使いません。使う MCP は利用者がデータの置き場の `mcp.json` に書き、名前はその `mcpServers` のキーです。MCP の使い分けを書いたファイルは、「[セキュリティの群の MCP の使い分け](security-mcp.md#セキュリティの群の-mcp-の使い分け)」の節で渡し方を述べます。

解析しない言語のソースのファイルか読めない記述が範囲にあるときは、費用の同意の後に「[解析しない言語の案内](#解析しない言語の案内)」と「[読めない記述の案内](#読めない記述の案内)」を出し、「それでもレビューを実施しますか」と尋ねます。y か yes のときだけ起動します。

終了コード 3 は、どの群でも、起動の失敗か天井による見送りがあり、指摘が欠けていることを表します。この回で最終的に通らなかった失敗が対象で、前の回から持ち越した失敗と、走らせ直して通った失敗は数えません。標準エラーに誤りの行を書きます。欠けの中身は `--json` の `failures` と `run.budget_stops` にあります。実装とセキュリティの群でシャードか検証の分割が失敗したときは、結果に未検証の候補を含みます。未検証の候補は、実装とセキュリティの群で「未検証」の区分の指摘として残り、必読に数えず、`show` で必読の直後の節に出ます。

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
jigane languages fetch [<言語> ...] [--accept-terms]
# <名前>: 追加の言語の名前
# <言語>: 取得する言語の名前（組み込みの言語か、有効にした追加の言語）。省略するとすべて
# --distribution: 同じ名前が複数の配布にあるときに、使う配布を選ぶ
# --accept-terms: 利用規約とプライバシーポリシーに同意したものとして、尋ねずに取得する
```

`list` は組み込みと追加の言語を一覧します。`enable` は追加の言語を有効にし、`disable` は外します。`fetch` は組み込みと有効な追加の言語の文法を取得します。名前を並べると、その言語だけを取得し、同じ名前を重ねても一度だけ取得します。何も書かなければ、すべてを取得します。知らない名前が一つでもあれば、同意を尋ねる前に、受けられる名前を示して終了コード 2 で止まり、何も取得しません。取得は置き場に文法を足すだけで、取得しなかった言語の文法を消しません。`list` の各行は、`--json` では `state` に `ready`（使える）、`not_enabled`（有効でない）、`grammar_absent`（文法を取得していない）、`unavailable`（使えない）のいずれかを持ちます。`grammar_absent` は、アダプタは読み込めていて文法の共有ライブラリが置き場に無いだけの言語で、組み込みの言語にも有効にした追加の言語にも付きます。誤りではないので `reason_kind` は `null`、`suffixes` は空で、`detail` に取得の案内（`jigane languages fetch <名前>`）の文を入れます。文字の出力では、見出しの行の次に「取得: jigane languages fetch <名前>」の行を出します。`reason_kind` が値を持つのは `unavailable` の行だけで、`grammar_error` は文法の宣言の誤りやハッシュの不一致など、本物の文法の誤りを表します。読む側は、`state` が `ready` 以外の行をすべて使えない言語として扱えば、新しい値が増えても誤りません。同意の手順は「[導入](../README.md#導入)」の節に、照合は「[文法の取得](data-destinations.md#文法の取得)」の節に、追加の言語の足し方は「[言語を追加する](adding-languages.md#言語を追加する)」の節にあります。
