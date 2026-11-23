# コマンドの使い方

[README に戻る](../README.md)

## コマンド一覧

副コマンドをレビューの段階ごとに並べています。端末では `jigane --help` で一覧を出せます。

<!-- BEGIN GENERATED: command-table (scripts/generate_command_table.py) -->

| 段階 | コマンド | モデルを呼び出す | 役割 |
| --- | --- | --- | --- |
| レビューを作る | `new` | いいえ | レビューを作ります |
| レビューを作る | `refresh` | いいえ | 作者の修正を取り込みます |
| レビューを作る | `list` | いいえ | レビューを一覧します |
| レビューを作る | `archive` | いいえ | レビューを一覧から隠します。データは残ります |
| レビューを作る | `unarchive` | いいえ | アーカイブしたレビューを一覧へ戻します |
| レビューを作る | `delete` | いいえ | レビューを消します。戻せません |
| 見る | `show` | いいえ | レビューを表示します |
| 見る | `diagrams` | いいえ | ツリー図、シーケンス図、方針のレビューの図を出します |
| 見る | `sequence` | いいえ | 変更した入口ごとのシーケンス図を出します |
| 見る | `technology-list` | いいえ | 変更したファイルが使う技術の表を出します |
| 見る | `design` | いいえ | 設計書を読みます |
| 見る | `estimate` | いいえ | レビュー資料の大きさと費用を見積もります |
| 見る | `usage` | いいえ | スキルの使用量と成果を表で示します |
| 解析する | `run` | はい | スキルを実行します |
| 解析する | `flaky` | いいえ | テストの flaky の検査だけを走らせます（レビューは作りません） |
| 指摘を選ぶ | `pick` | いいえ | 指摘（F、B の番号）を最終的な指摘に加えます |
| 指摘を選ぶ | `unpick` | いいえ | 指摘（F、B の番号）を最終的な指摘から外します |
| 指摘を選ぶ | `hold` | いいえ | 指摘（F、B の番号）を保留にします。--reason で理由を残せます（任意） |
| 指摘を選ぶ | `unhold` | いいえ | 指摘（F、B の番号）を保留から外します |
| 指摘を選ぶ | `object` | いいえ | 指摘への異議を記録します |
| 指摘を選ぶ | `unobject` | いいえ | 指摘への異議を取り下げます |
| 書き出す | `export-reply` | いいえ | 最終的な指摘から返信の文面を作ります（差分のレビュー） |
| 書き出す | `export-report` | いいえ | 最終的な指摘からレポートを書き出します |
| 書き出す | `templates` | いいえ | 返信とレポートのテンプレートを一覧します |
| 導入 | `select-agent` | いいえ | レビューエージェントを選択・確認します |
| 導入 | `languages` | いいえ | 言語を一覧し、追加の言語を有効にし、外し、文法を取得します |
| 確かめる | `debug` | はい | jigane の内部を確かめます。出力は暫定で、jigane の版で変わることがあります |

<!-- END GENERATED: command-table -->

`new pr` と PR のレビューの `refresh` は GitHub から情報を読み、`languages fetch` は tree-sitter の文法を取得します。[データの送り先](data-destinations.md)

## レビューの識別子

`new` の出力に、作ったレビューの識別子を示します。以下の例で `<レビュー>` はこの識別子です。先頭の行がコマンドの呼び方で、続く `#` の行が引数の意味です。

| レビューの種類 | 識別子 |
| --- | --- |
| PR | `<owner>/<repo>#<番号>` |
| 範囲 | `<owner>/<repo>@<名前>` |
| ファイル | `<owner>/<repo>:<名前>` |
| 設計書 | `<owner>/<repo>+<名前>` |

呼び出した場所のリポジトリで名前が一つに決まるときは、名前だけ（例 `payment`）で指せます。同じリポジトリに範囲とファイルのレビューなどが同じ名前で複数あれば、入力を拒んで候補を示します。同じリポジトリにその名前が無いときも入力を拒み、別のリポジトリに同じ名前のレビューがあれば候補を示します。

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
| 名前を取れない演算子関数 | アダプタが申告した言語（組み込みでは Swift） | `static func +` のように、関数の名前が識別子でない |
| 名前を取れない宣言子 | アダプタが申告した言語（組み込みでは C#） | クラス直下の `Foo.Bar(x);` のように、名前を持たない宣言子 |

構文エラーのファイルには、後ろの二つの規則を当てません。案内には、規則ごとの件数と最初の五つの `経路:行` を示します。変更前の木の記述には「変更前」が付きます。案内の末尾には、当たった種類ごとに直し方を一度だけ示します（警告の説明も同じ文です）。同じ検出を、レビューの結果に警告として保存します。`jigane show` の警告の列と、`--json` の `gates.warnings` に、`UNREADABLE-SYNTAX-ERROR`、`UNREADABLE-SWIFT-OPERATOR-FUNCTION`、`UNREADABLE-CSHARP-NAMELESS-DECLARATOR`、プラグインの言語が申告した `UNREADABLE-<言語名>-<種類>` の項目（`<言語名>` と `<種類>` は大文字で、`_` は `-` に置き換わります。組み方は [adding-languages.md](adding-languages.md#サポートしない構文の申告) にあります）が並びます。`source` は `unreadable` で、項目には記述のある側（`side`: `before` か `after`）と該当行の本文が載ります。警告は変更した行に絞らず、`new` と `refresh` の時点の木から作ります。終了コードは変わらず、警告は数えません。新しいオプションはありません。`run` は、解析しない言語の案内と合わせて、続けるかを一度だけ尋ねます。

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
# --name: レビューの名前。省くと先端の名前から作る。同じ名前が既にあるときに別の名前を付ける。使える文字は下の規則に従う
# --intent: 変更の意図の文。--intent-file と同時には渡せない
# --intent-file: 変更の意図を書いたファイル
# --json: 結果を JSON で出す
# --sarif: 静的検査の SARIF。繰り返して渡せる。先端のコミットで走らせたものを渡す
# --sarif-root: SARIF のパスの起点。省くと呼び出した場所を含む git のリポジトリの根
```

レビューの名前（`--name`）に使える文字は、英数字（`A-Z`、`a-z`、`0-9`）と `_`、`.`、`-` です。先頭は英数字か `_` で、`.` と `-` で始まる名前は使えません。長さの上限はありません。`new files` と `new design-doc` の `--name` も同じ文字の規則です。たとえば `fix_1.2-a` は使え、`.hidden`、`-x`、`a b`、`a/b`、`..` は使えません。

数字だけの名前は、保存先が PR の番号と重なるかどうかで種類ごとに違います。PR の番号は GitHub が振るので、名前で作る側が譲ります。`new files` は保存先が名前そのままなので、数字だけの名前（`123` など）を拒みます。`new range`（保存先は `@` で始まる）と `new design-doc`（保存先は `+` で始まる）は PR の番号と重ならないので、数字だけの名前も使えます。

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

レビューを一覧します。`--archived` を省くとアーカイブしていないレビュー、付けるとアーカイブしたレビューだけを出します。両方を合わせた一覧を出す値はありません。

古い版などで読めない記録は、`jigane list` が 1 件ずつ理由つきで警告します。`list` 以外の副コマンドは、置き場の全件を内部で引く間の同じ警告を、出力の末尾の 1 行にまとめます。一回の実行で置き場を何度引いても同じ記録は一度だけ数え、件数と、理由を見る `jigane list` だけを示します。

`--json` の出力は、レビューごとに 1 要素を持つ配列です。各要素は `"id"` の鍵を持ち、値は [レビューの識別子](#レビューの識別子) の文字列です（例 `owner/repo@name`）。`--archived` を付けたときの一覧も、同じ形の要素の配列です。

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
# --max-budget-usd-per-call: 1 回の起動あたりの上限の目安（有限で 0 より大きい値だけを受ける）。この値で上限を決めた見積もりを出す
# --mcps: セキュリティの群だけが使う MCP の名前
# --mcp-guide: セキュリティの群だけが使う、MCP の使い分けの TOML
```

レビュー資料の大きさと費用を見積もります。引数は `run` と同じ意味で、`--model` は受けません。

`--json` の `expected` には、群の見込みを `total_usd`（発見役の見積もり × 検証役の倍率）、`version`、`discovery_estimate_usd`、`verifier_multiplier`、`verifier_multiplier_source` で出します。方針は検証役を持たないので倍率は 1 で、出所は `no_verifier` です。Claude Code の実装とセキュリティの倍率は、レビューの置き場の `runs.json` にある同じリポジトリの回の記録から引きます（`run_records`）。`jigane run` は回の記録へ、起動の前の発見役の見積もり（`discovery_estimate_usd`）と見積もりの数え方の版（`estimate_version`）を足します。倍率は、今の版の欄を持つ回について、群の実額を発見役の見積もりで割って平均した値です。欄の無い古い回と版の違う回は数えません。数える回が無ければ既定の 2.5 です（`default`）。`jigane usage` の出力には列を足しません。

Codex の `estimate --json` は、初期起動数を `planned_invocations`、各プロンプトの UTF-8 のバイト数を `prompt_bytes` に出します。バイト数には jigane が付けるレビューの指示を含みます。`launches` には初期起動ごとの担当モデルと本文の入力概算が入ります。群の USD 換算見込み（`expected.total_usd`）では、初期起動ごとに入力と、出力・推論を合わせた 8,192 トークンを担当モデルの単価で見積もります。その合計に、方針は 1、実装とセキュリティは追加起動を見込んで 2.5 を掛けます。MCP を指定した場合や見積もれない場合は `expected.total_usd` が `null` になります。Codex の `max_budget_usd_per_call` は常に `null` です。

Codex の `noninteractive_run_available` は、MCP の指定がなく、初期起動の資料から群の見込みを算出できた場合に `true` になります。`noninteractive_requires` と `noninteractive_model` は、非対話実行に使う認証方法と既定のモデルを示し、MCP を指定すると両方が `null` になります。[非対話実行](#jigane-debug) の起動前には、ログインを確認し、群の見込みを `--max-usd` と照合します。Codex は `estimate` の `--effort` と `--max-budget-usd-per-call` を受け付けません。

解析しない言語のソースのファイルが範囲にあるときは、「[解析しない言語の案内](#解析しない言語の案内)」と、読めない記述があるときは「[読めない記述の案内](#読めない記述の案内)」を出します。

### jigane usage

```bash
jigane usage <レビュー> [--json | --csv] [--wide] [--no-header]
# --json、--csv: 表の代わりに出す形式。常に全列
# --wide: 表に全ての列を出す
# --no-header: --csv で見出しの行を出さない
```

スキルの使用量と成果を表で示します。

## エージェントを選ぶ

### jigane select-agent

```bash
jigane select-agent [claude|codex]
jigane select-agent --show [--json]
```

引数を省くと端末で Claude Code と Codex のどちらを使うか尋ねます。エージェント名を指定して保存することもできます。選択の変更には同じコマンドを使います。[データの置き場](#データの置き場) の直下の `agent.json` にはエージェント名だけを保存します。

`--show --json` は、保存された選択を `{"agent":"claude"}` または `{"agent":"codex"}` として出力します。未選択なら `{"agent":null}` を返します。設定が壊れている場合は診断を出して終了コード 2 で止まり、明示指定で選択し直せます。`--show` は設定を読み取るだけです。

## 解析する

### jigane run

```bash
jigane run <レビュー> macro|micro|security [--model <モデル>] [--effort low|medium|high|xhigh|max] [--max-budget-usd-per-call <USD>] [--mcps <名前>...] [--mcp-guide <パス>] [--json]
# macro|micro|security: 走らせる群
# --model: その回の全ての役のモデル。省くと群の既定
# --effort: Claude Code CLI の effort。省くと群の既定
# --max-budget-usd-per-call: 1 回の起動あたりの上限の目安（USD。有限で 0 より大きい値だけを受ける）
# --mcps: セキュリティの群だけが使う MCP の名前
# --mcp-guide: セキュリティの群だけが使う、MCP の使い分けの TOML
```

保存したエージェントで実行します。未選択の場合はレビューを読む前に止まり、`jigane select-agent` を案内します。環境変数 `JIGANE_AGENT` と PATH による自動選択は Make／Just のセットアップで使います。見積もり、作成、一覧、help は選択前から使えます。

macOS と Linux では Codex を選んで `macro`、`micro`、`security` を 1 群ずつ実行できます。端末の `jigane run` では `codex login` で保存した ChatGPT ログインを使います。起動前に OpenAI への送信の同意を端末で求めます。Codex は `--model`、`--effort`、`--max-budget-usd-per-call` を受け付けません。実行後は報告された使用量から USD 換算額を記録します。[送る資料と送信先](data-destinations.md#codex-のレビュー)

Claude Code の実行の前には費用の同意を尋ねます。問いには「発見役:」の行に続けて、「群の見込み: …」の行で発見役の見積もりと検証役の倍率、その積を示します。

スキルを実行します。群は `macro`（方針）、`micro`（実装）、`security`（セキュリティ）の三つです。`--max-budget-usd-per-call` は Claude Code CLI の 1 回の起動あたりの上限の目安であり、超えることがあります（上限は API の呼び出しごとに確かめるため、最後の一回の分だけ超えうる）。実行全体の上限でもありません。方針は PR と範囲のレビューで最大 5 回起動するので、`--max-budget-usd-per-call` を渡すとそのすべてに効き、実行全体の目安は最大でその 5 倍になります。上限に達する入力の起動は見送られ、残りの結果を保存します。ただし方針で図の起動が走らなかった回は、前の回の結果を残します（[reviews.md](reviews.md) の方針の節）。見送った起動は、`jigane run` の表示と `jigane show` の表示に「起動せず」の行で出ます。設計書のレビューは起動が 1 回なので、この目安が実行全体の目安になります。見込みが目安以上のときは、警告して続けます。

既定は群ごとに持ちます。実装とセキュリティの群は 0.5 USD です。方針は、PR と範囲のレビューでは図の起動が 0.36 USD、原則の起動が 1 回あたり 0.04 USD で、全起動の和の目安は 0.52 USD です。原則の起動は、その原則を問える形が図にあるときだけ走るので、実際の起動は最大 5 回です。設計書のレビューは 1 回の起動で 0.5 USD です。上限と effort の既定は設定ファイルから読まず、変えるときは引数で渡します。

Claude Code の方針の図の形と設計書の形は、プロンプトの大きさと出力の見込みから見積もった額が 1 本の上限以上の起動を見送ります。式と、見送りが始まる大きさは [cost.md](cost.md) にあります。`jigane estimate` の `input_cost_usd` は、図の形と設計書の形では出力の見込みを含む額です。

モデルも既定を持ちます。実装とセキュリティの群と、方針の図の起動と設計書のレビューは claude-sonnet-5-5 で、方針の原則の起動は claude-haiku-5-5 です。effort は方針が high で、他は渡しません。`--model` と `--effort`（low、medium、high、xhigh、max）で上書きできます。`--model` は方針の図と原則のすべての起動に効きます。起動ごとのモデルは `runs.json` に残り、`jigane usage` は起動のモデルごとに行を分けます。`jigane estimate` も `--effort` を受け、見積もりに解決した effort を出します。実行全体の金額と起動の回数は `run` の出力に示します。

端末の `jigane run` のセキュリティの群では、`--mcps <名前>...` で基準資料を参照するサーバを選びます。省略すると MCP を使いません。選んだ CLI が設定の読み込みやサーバの起動を担います。エージェントごとの登録手順と `--mcp-guide` の書き方は [MCP の設定](security-mcp.md) を参照してください。

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
jigane hold <レビュー> <指摘>... [--reason <理由>] [--json]
jigane unhold <レビュー> <指摘>... [--json]
# <指摘>...: 指摘の番号（F1、B2 など）
# --reason: 保留の理由（任意）。並べたすべての指摘に同じ理由を記録する
```

`hold` は指摘を保留にします。`unhold` は保留から外します。

`--reason` を付けない `hold` は、これまでどおり成功します。すでに保留の指摘へ理由なしで打ったときは、何も変えず前の理由を残します。理由は空白だけにできません。空白だけの理由を渡すと「保留の理由が空です。理由を書くか、--reason を付けずに実行してください。」と表示して失敗し、何も記録しません。理由は `object` の `--reason` と同じく、`show` の表示には出ません。`--json` の出力と `show --json` の選択の欄には、保留の番号の配列 `held` はそのままに、理由を持つ保留だけを番号から理由への対応 `hold_reasons` で出します。理由を付けていない保留は `hold_reasons` に現れません。

`unhold` は保留を外すので、理由も消えます。理由を持つ保留を、別の扱い（`pick` や `object`）で置き換えたとき、または別の理由を付けた `hold --reason` で書き換えたときは、消える理由を標準エラーに `注: F1 の保留を置き換えました。消えた理由: ...` の一行で示します。異議を `hold` で置き換えたときは、同じ形で異議の理由を示します。理由のない保留が置き換わるときは、何も出しません。

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

`list` は組み込みと追加の言語を一覧します。`enable` は追加の言語を有効にし、`disable` は外します。`fetch` は組み込みと有効な追加の言語の文法を取得します。名前を並べると、その言語だけを取得し、同じ名前を重ねても一度だけ取得します。何も書かなければ、すべてを取得します。知らない名前が一つでもあれば、同意を尋ねる前に、受けられる名前を示して終了コード 2 で止まり、何も取得しません。取得は置き場に文法を足すだけで、取得しなかった言語の文法を消しません。組み込みの文法の取得に失敗したときは、追加の言語を取得せずに止まり、処理の失敗の終了コードで終わります。打ち直すと最初から取り直します。`list` の各行は、`--json` では `state` に `ready`（使える）、`not_enabled`（有効でない）、`grammar_absent`（文法を取得していない）、`unavailable`（使えない）のいずれかを持ちます。`grammar_absent` は、アダプタは読み込めていて文法の共有ライブラリが置き場に無いだけの言語で、組み込みの言語にも有効にした追加の言語にも付きます。誤りではないので `reason_kind` は `null`、`suffixes` は空で、`detail` に取得の案内（`jigane languages fetch <名前>`）の文を入れます。文字の出力では、見出しの行の次に「取得: jigane languages fetch <名前>」の行を出します。`reason_kind` が値を持つのは `unavailable` の行だけで、`grammar_error` は文法の宣言の誤りやハッシュの不一致など、本物の文法の誤りを表します。読む側は、`state` が `ready` 以外の行をすべて使えない言語として扱えば、新しい値が増えても誤りません。同意の手順は「[導入](../README.md#導入)」の節に、照合は「[文法の取得](data-destinations.md#文法の取得)」の節に、追加の言語の足し方は「[言語を追加する](adding-languages.md#言語を追加する)」の節にあります。

## 確かめる

### jigane debug

```bash
jigane debug errors [--json]
jigane debug prompt <レビュー> <群> [--mcps <名前>...] [--mcp-guide <パス>] [--max-budget-usd-per-call <USD>] [--page-size <件数>] [--after <印>] [--json]
jigane debug run <レビュー> <群> --yes --max-usd <額> [--expect-agent claude|codex] [--effort <effort>] [--mcps <名前>...] [--mcp-guide <パス>] [--json]
jigane debug stats [--json] [--page-size <件数>] [--after <印>] [--with-body]
jigane debug review-id [--json]
jigane debug paths [--json]
```

jigane の内部を確かめる口です。`pip debug` に倣い、利用者と開発者が使います。**出力は暫定で、jigane の版で変わることがあります。** 確かめる対象は `errors` と `stats` の他にも増えます。

`errors` は、jigane が失敗で返す種類を一覧します。`--json` の出力は `{"fields": [...], "kinds": [{"kind": ..., "description": ..., "extra_fields": [...]}, ...]}` です。`fields` は失敗の JSON（`--json` の失敗の出力）がすべての種類で持つ鍵です。`kinds` は `kind` の取りうる値と、その見出しです。`extra_fields` は、その種類の失敗だけが `fields` に足す鍵で、足さない種類では空の配列です（`review_exists` は `["review"]`）。どちらもコードの表から機械的に作ります。`--json` が無いときは、種類と見出しの表を出します。

`prompt` は、LLM を起動せずに、その群で起動するときのプロンプトの本文を出します。組み立ては `estimate` と `run` が使うものと同じで、費用はかからず、確認なしで走ります。本文はレビュー対象の本文を含むので、標準出力へ出すだけで、ファイルへは書きません。`--json` の出力は `{"prompts": [{"role": ..., "text": ..., "bytes": ...}, ...], "next": ...}` です。`role` は起動の種類（発見役は `discovery`、原則の起動は `principle`）、`bytes` は `text` の UTF-8 のバイト数です。`--page-size`（既定 5、上限 20）の件数ずつに分け、`next` を `--after` に渡すと続きを読みます。`next` が `null` なら末尾です。`--mcps`（セキュリティの群の MCP の注記を変えます）と `--max-budget-usd-per-call`（上限に収まるまでシャードを増やすので、分け方を変えます）は `estimate` と同じ意味です。`--max-budget-usd-per-call` は有限で 0 より大きい値だけを受け、`nan`、`inf`、0、負の値、数値でない値は終了コード 64 で止まります（`run` と `estimate` では引数の誤りの終了コード 2）。`--page-size` か `--after` が正しくなければ、終了コード 64 で止まります。

`run` は実験的な非対話の入口です。`macro`、`micro`、`security` から 1 群を指定します。未選択の場合は `jigane select-agent` を案内します。`--expect-agent` を指定すると、保存したエージェントが指定と異なる場合は実行前に止まります。AI の道具は実行前に `jigane estimate` の表示と指定する上限を人に示し、資料の送信と費用の同意を得てください。費用と解析しない言語のファイルに関する端末の問いを省略します。`--yes` と `--max-usd` の両方が必要です。引数が不足すると終了コード 64 です。`--model` と `--max-budget-usd-per-call` は受けません。

Codex の非対話実行では MCP の指定を拒否します。資料を参照するには、端末の `jigane run <レビュー> security --mcps <名前>` を使います。

Claude Code と Codex のどちらでも、群の見込みを `--max-usd` と比べます。見込みが上限を超えるか、見積もれない場合は、資料を送らず終了コード 2 になります。Codex は 1 USD 以下を指定し、Codex CLI に保存した ChatGPT ログインを使います。ログインを確認できない場合も資料を送らず終了コード 2 になります。`--max-usd` は実行前の判定に使い、実行後の使用量と USD 換算額は通常の `run` と同様に記録します。実行後の換算額は出力の量や検証の回数によって事前見込みや指定額を超えることがあります。

`--json` の出力は `jigane run --json` の鍵に `max_usd`、群の見込みを表す `estimated_usd`、`experimental`（常に `true`）を足したものです。見込みが指定額を超えた場合は、この三つの鍵と `stopped: estimate_exceeds_max_usd` を返します。Codex に 1 USD を超える額を指定した場合は `estimated_usd` が `null` になり、`stopped: confirmation_required` を返します。認証や見積もりを確認できない場合は `error` を返します。

`stats` は、置き場の全レビュー（アーカイブ済みを含む）について、指摘の数と札をまとめて返します。既定は数と札（`id`、`severity`、`triage`）だけで、指摘の本文（`claim`）は `--with-body` を付けたときだけ出します。`--json` の出力は `{"reviews": [...], "next": ..., "skipped": ..., "limits": {...}}` です。`reviews` の各要素は `id`、群ごとの指摘の数 `finding_counts`、群ごとの指摘の札 `findings` を持ちます。`--page-size`（既定 20、最大 100）で一度に出す件数を決め、`next` が `null` でなければ、その値を `--after` に渡すと続きを取れます。印はレビューの識別子の並びで決めるので、間にレビューが増えても重複や抜けは起きません。読めない記録と、`findings.json` が `limits` の `max_findings_file_bytes`（4 MiB）を超える記録は、読み込む前に飛ばします。飛ばした記録は `skipped` に数えます。`skipped` は、そのページを作る間に飛ばした数です。読めないレビュー自体（`review.json` の破損）は並びに置けないので、最初のページ（`--after` が無いとき）だけで数えます。したがって、全ページの `skipped` の和が全体の数になります。

`review-id` は、レビューの識別子（`owner/repo` に区切りとキーを続けた形）の文法を示します。`--json` の出力は `{"pattern": ..., "kinds": [{"kind": ..., "form": ...}, ...]}` です。`pattern` は識別子全体に当てる正規表現、`kinds` は種類（`ReviewKind` の値）とその形で、区切りは種類ごとに違います。キーの規則も種類ごとに違い（PR は数字、それ以外は名前）、`pattern` には含まれません。識別子の長さの上限はコードに無いため、`max_length` は出しません。どちらも `ReviewKind` と識別子の文法から機械的に作ります。

`paths` は、jigane が読み書きする置き場のパスと、データの置き場の根の検査の結果を示します。根を作らず、何も書きません。`--json` の出力は `{"data_home": ..., "data_home_check": {"exists": ..., "stops": ..., "kind": ...}, "repository_docs_settings": ...}` です。`data_home` は「[データの置き場](#データの置き場)」の順で決まる根の絶対パス、`repository_docs_settings` は根の下の `repository-docs.toml` の絶対パスで、ファイルが無くても同じパスを返します。`data_home_check` は、レビューを書き込む副コマンドが根に当てる検査と同じ判定です。`exists` は根があるか、`stops` はその検査で書き込みの副コマンドが止まるかを表します。`kind` は判定の理由で、次のいずれかです。

| `kind` | 意味 | `stops` |
| --- | --- | --- |
| `ok` | 所有者が実行ユーザーで、group と other に書き込み権限が無い | `false` |
| `absent` | 根が無い。書き込みの副コマンドが 0700 で作る | `false` |
| `owner_mismatch` | 根の所有者が実行ユーザーと違う | `true` |
| `writable_by_others` | group か other に書き込み権限がある | `true` |
| `not_checked` | Windows。権限は ACL に任せ、検査しない | `false` |

根が symlink のときは、その先を調べます。読む側は、`kind` の値が増えても誤らないよう、止まるかどうかを `stops` で判定してください。`--json` が無いときは、三つを一行ずつ出します。

`make debug-errors ARGS="--json"`、`make debug-review-id ARGS="--json"`、`make debug-paths ARGS="--json"` でも走らせられます。

`debug run` は、`--yes` と有限で 0 より大きい `--max-usd` が揃い、見込みが上限以内のときだけ起動します。それ以外は何も起動せず、次の終了コードで止まります。

| 入力 | 結果 |
| --- | --- |
| `--yes` が無い | 終了コード 64 |
| `--max-usd` が無い | 終了コード 64 |
| `--max-usd` の値が不正（`nan`、`inf`、0、負の値、数値でない） | 終了コード 64 |
| Codex で `--max-usd` が 1 USD を超える | 終了コード 2。起動しない |
| 見込みが `--max-usd` を超える | 終了コード 2。起動しない |
| 見込みが `--max-usd` 以内で、Codex ではログインも確認できる | 起動する。終了コードは `jigane run` と同じ（0、2、3） |

## データの置き場

レビュー、設定ファイル、取得した文法は、データの置き場の根の下に置きます。根は次の順で決まり、先に当てはまったものを使います。

1. 環境変数 `JIGANE_HOME` が空でなければ、その値。
2. Windows では、`%LOCALAPPDATA%` の下の `jigane`。`LOCALAPPDATA` が設定されていない環境は想定せず、根を決められずに失敗します。
3. Windows 以外では、環境変数 `XDG_DATA_HOME` の下の `jigane`。`XDG_DATA_HOME` が空または未設定なら、`~/.local/share` の下の `jigane`。

`JIGANE_HOME` は、Windows でも Windows 以外でも、`LOCALAPPDATA` と `XDG_DATA_HOME` に優先します。根が無ければ、jigane は他の利用者に読ませない権限（0700）で作ります。既にある根の権限は変えません。ただし、group か other に書き込み権限がある場合は、「データの置き場の権限が広すぎます。chmod 700 で狭めてください」と表示して止まります。止まったら `chmod 700 <根>` で狭めてください。既にある根の所有者が実行しているユーザーでない場合は、「データの置き場の所有者が実行ユーザーと違います。所有者のユーザーで実行するか、置き場を自分の所有に移してください」と表示して止まります。chmod では直せないので、所有者のユーザーで実行するか、根を自分の所有に移してください。読み取りの権限だけなら止まりませんが、置き場には差分、PR の本文、Claude の出力が平文で入るので、group や other に読める権限（0755 など）のままだと、他のユーザーに内容を読まれるおそれがあります。`chmod 700 <根>` で狭めてください。レビューを書き込む副コマンドは、根の権限が広いと止まります。`show` や `list` などの読み取りは止まりません。Windows の権限は ACL で決まるので、この検査をしません。根までの途中のディレクトリが無いときは、jigane が新しく作ったものだけを 0700 で作り、既にある親の権限は変えません。レビューのディレクトリも 0700 で作ります。

## リポジトリの文書を渡す

リポジトリが自分で定めた開発方針とセキュリティ指針の文書を、レビューのプロンプトへ渡せます。設定はデータの置き場の根の `repository-docs.toml` に書きます。レビュー対象の木の中の設定ファイルからは読みません。ファイルが無ければ、文書を渡しません。

```toml
# 最上位のキーは、表の無いリポジトリすべての既定
development_docs_path = "/path/to/app/docs/development.md"

# リポジトリごとの表。キーの単位で最上位を上書きする
[repositories."owner/repo"]
development_docs_path = "/path/to/repo/CONTRIBUTING.md"
security_docs_path = "/path/to/repo/SECURITY.md"
```

キーは次の二つです。値は文字列の絶対パスで、空文字列は指定の無いものとして扱います。

| キー | 渡す文書 | 入る群 |
| --- | --- | --- |
| `development_docs_path` | リポジトリの開発方針 | 方針、実装、セキュリティのすべての群 |
| `security_docs_path` | リポジトリのセキュリティ指針 | セキュリティの群だけ |

リポジトリの表と最上位は、キーの単位で引きます。表にあるキーは表の値を、表に無いキーは最上位の値を使います。表の名前は `owner/repo` の形で、大文字小文字を区別せずに比べます。

パスがレビュー対象のリポジトリのチェックアウトの中を指すなら、その場所の今のファイルではなく、レビューの起点の内容を読みます。PR と範囲のレビューでは起点のコミットの木を、起点の無いレビューではチェックアウトの HEAD のコミットを読みます。レビューする変更がその文書を書き換えていても、変更前の文書を渡します。リポジトリの外を指すパスは、書かれたとおりのファイルを読みます。

次の場合は、その文書を渡さず、標準エラーに一行の警告を出してレビューを続けます。絶対パスでない、秘密のファイル名（`.env` など）に当たる、ファイルが無い、起点に無い、起点でシンボリックリンクである、通常のファイルでない、32,768 バイトを超える、UTF-8 でない、NUL を含む、読めない。

設定ファイルそのものに誤りがあるときは、どの文書を指したのかが分からないので、レビューを止めます。TOML として読めない、知らないキーがある、値が文字列でない、`repositories` が表でない、表の名前が `owner/repo` の形でない、大文字小文字だけが違う表が二つある、という場合です。
