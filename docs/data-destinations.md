# データの送り先

[README に戻る](../README.md)

jigane が外へ送る相手の一覧です。ここに無い相手へ、jigane が自分で接続することはありません。

レビューの対象の差分、PR の本文とレビューボットのコメント、周辺のファイルは、利用者が導入した Claude Code を通じて Anthropic に送られます。学習への利用と保持の期間は、利用者自身の Anthropic のアカウントの設定と契約に従います。詳しくは <https://code.claude.com/docs/en/data-usage> を読んでください。機密のリポジトリでは、契約と設定を確かめてから使ってください。

## 送り先

| ホスト | 送るもの | 時機 | 口 |
| --- | --- | --- | --- |
| `github.com` | gh と git の取得の要求（ref と commit の SHA）と、gh と git の認証。文法の配布物の取得の要求 | `jigane new pr`、`jigane refresh`（gh と git）。`jigane languages fetch`（文法） | gh、git、tree-sitter-language-pack |
| `api.github.com` | リポジトリと PR の番号、gh の認証 | `jigane new pr`、`jigane refresh` | gh |
| `release-assets.githubusercontent.com` | 文法の配布物の取得の要求 | `jigane languages fetch` | tree-sitter-language-pack |
| （claude を通して） | プロンプト（差分、PR の題と本文、ボットのコメント、図と表、前の回の指摘）、claude が読んだファイルの中身、モデルの出力。宛先は claude が決める。このほか claude 自身が、環境の節（作業ディレクトリのパス、OS の名前と版）と、ログイン中のアカウントのメールアドレスを要求に付け足します。環境の節とメールアドレスは jigane の指定では止まらず、切る手段は確かめた範囲で見つかりませんでした。claude 2.1.308 にログインした環境で、起動の環境を `HOME`、`PATH`、`USER`、`LOGNAME`、`TMPDIR` だけに絞っても入ること、`ANTHROPIC_API_KEY` で認証しても入ることを確かめました。作業ディレクトリが Git のリポジトリのときに付くリポジトリの状態の節（Git の利用者名、枝、状態、直近のコミットの件名）は、jigane が `--settings` に `includeGitInstructions: false` を渡して切っています。claude 2.1.308 で要求から消えることを確かめました | `jigane run` の各起動 | 起動する外部コマンド claude |
| （利用者が選ぶ MCP サーバ） | モデルがその MCP サーバの道具に渡す問い。宛先は利用者の `mcp.json` の設定が決める | `jigane run security --mcps <名前>` を指定したときだけ | claude が呼ぶ MCP サーバ |

ホストの列が括弧で始まる行は、ホスト名を持ちません。送り先が jigane の外（claude の設定や利用者の MCP の設定）で決まるためです。

gh と git が `github.com` へ送るのは取得の要求と認証で、レビュー対象のコードは受け取るだけです。gh が呼ぶコマンドは「[jigane が呼ぶ gh のコマンド](#jigane-が呼ぶ-gh-のコマンド)」の節にあります。gh は `GH_HOST=github.com` に固定され、`GH_REPO` は渡されません。

文法の取得は `jigane languages fetch` のときだけです。レビューの実行のときには取得しません。

## 付随の送信

Claude Code は、プロンプトのほかに、使用の統計、誤りの報告、自動の更新の確かめ、機能の旗の取得などを送ることがあります。jigane は、起動する claude の環境に `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` を入れ、これらを止めます。利用者の環境に同じ変数が別の値で入っていても、jigane は 1 に上書きします。止めた後、claude の送り先は Anthropic のモデルの API だけになります（次の節の環境変数で差し替えた場合は、その先になります）。

## 環境変数による差し替え

次の変数は、利用者の環境の値のまま子のプロセスに渡ります。jigane は除きも上書きもしません。

- `ANTHROPIC_BASE_URL`：claude の送り先
- `HTTPS_PROXY` などのプロキシの変数：claude、gh、git、文法の取得の接続
- `OTEL_*`：claude の OpenTelemetry の送り先
- `TREE_SITTER_LANGUAGE_PACK_MANIFEST_URL`：文法の取得先

claude の子の環境から除くのは、GitHub の資格情報の変数（`GH_TOKEN`、`GITHUB_TOKEN`、`GH_ENTERPRISE_TOKEN`、`GITHUB_ENTERPRISE_TOKEN`）だけです。gh と git の子の環境からは、この除きは行いません。

## ユーザーの設定ファイル

`~/.claude/settings.json` の `env` は、jigane が起動する claude には効きません。jigane は claude を `--restricted` で起動し、ユーザー、プロジェクト、ローカルの設定ファイルを読ませないためです。環境変数で送り先や付随の送信を変えるには、jigane を起動するシェルの環境に設定します。

## jigane が呼ぶ gh のコマンド

gh は利用者の認証で書き込みもできますが、jigane は読むだけで、書き込みません。呼ぶ形は次の三つに限り、コードの許可リストに合わない gh は起動する前にエラーにします。問い合わせ先は、環境変数 GH_HOST の値によらず github.com に固定し、GH_REPO は渡しません。

PR の情報を読みます。`jigane new pr` と、PR のレビューの `jigane refresh` が呼びます。

```bash
gh pr view <番号> -R <owner>/<repo> --json number,url,title,body,baseRefOid,headRefOid,author
```

リポジトリを複製します。`jigane new pr` と、PR のレビューの `jigane refresh` が、データの置き場にそのリポジトリのキャッシュが無いときに呼びます。

```bash
gh repo clone <owner>/<repo> <置き場> -- --bare --filter=blob:none
```

レビューボットのコメントを読みます。`jigane new pr` と、PR の先端のコミットが進んだときの PR のレビューの `jigane refresh` が呼びます。

```bash
gh api --method GET --paginate --slurp repos/<owner>/<repo>/pulls/<番号>/comments
```

このコマンドが読むのは、PR の行に付いたコメント（インラインコメント）だけです。このうち背景に入れるのは、レビューボットが書いた、行に付いたスレッドの先頭のコメントです。レビューボットとは、GitHub が Bot と示すアカウントと、`.jigane.toml` の `bots` に書いたアカウントです。本文は先頭の 2000 文字までを入れます。次のものは背景に入れません。

- スレッドへの返信。作者がボットの指摘に答えた返信（採否とその理由）も入りません。
- ファイル単位のコメントと、行を持たないコメント。
- 人が書いたインラインコメント。
- PR の会話のコメント（`gh pr comment` で書くもの）と、レビューの本文。このコマンドでは読みません。

## 文法の取得

言語の文法は `jigane languages fetch` のときに GitHub のリリースから取得し、記録した sha256 と照合します。レビューの実行のときには取得しません。取得する言語は名前で選べ、送り先は選んでも変わりません。取得の前の同意は「[導入](../README.md#導入)」の節に書いています。追加の言語の文法も、プラグインが宣言したパックの版と sha256 に合わなければ読み込みません。

文法の sha256 の記録がある platform は次のとおりです。

- `linux-aarch64`
- `linux-x86_64`
- `macos-arm64`
- `macos-x86_64`
- `windows-aarch64`
- `windows-x86_64`

記録の無い platform では照合できないため、組み込みの文法を読み込まずに止めます。その platform への対応は Issue で知らせてください。

文法は利用者の手元で取得します。各文法のライセンスは次のとおりです。文法の共有ライブラリは tree-sitter-language-pack 1.22.1（MIT、[LICENSE](https://github.com/xberg-io/tree-sitter-language-pack/blob/v1.22.1/LICENSE)）が配り、各文法の元の版は、そのタグ v1.22.1 の `crates/ts-pack-core/language_definitions.json` の `rev` に従います。共有ライブラリがその版から作られたことは、ビルドを再現しては確かめていません。

| 言語 | リポジトリ | 版（コミット） | ライセンス | LICENSE |
|---|---|---|---|---|
| python | [tree-sitter/tree-sitter-python](https://github.com/tree-sitter/tree-sitter-python) | `26855eabccb1` | MIT | [LICENSE](https://github.com/tree-sitter/tree-sitter-python/blob/26855eabccb19c6abf499fbc5b8dc7cc9ab8bc64/LICENSE) |
| java | [tree-sitter/tree-sitter-java](https://github.com/tree-sitter/tree-sitter-java) | `e10607b45ff7` | MIT | [LICENSE](https://github.com/tree-sitter/tree-sitter-java/blob/e10607b45ff745f5f876bfa3e94fbcc6b44bdc11/LICENSE) |
| swift | [alex-pinkus/tree-sitter-swift](https://github.com/alex-pinkus/tree-sitter-swift) | `d36d45270eae` | MIT | [LICENSE](https://github.com/alex-pinkus/tree-sitter-swift/blob/d36d45270eae98b197cb6d8f133ae43e3586b83c/LICENSE) |
| csharp | [tree-sitter/tree-sitter-c-sharp](https://github.com/tree-sitter/tree-sitter-c-sharp) | `8c0abe0b84a3` | MIT | [LICENSE](https://github.com/tree-sitter/tree-sitter-c-sharp/blob/8c0abe0b84a3681d3e6852ce2e2a1eecb4f731c5/LICENSE) |
| kotlin | [fwcd/tree-sitter-kotlin](https://github.com/fwcd/tree-sitter-kotlin) | `1852ea17b7f6` | MIT | [LICENSE](https://github.com/fwcd/tree-sitter-kotlin/blob/1852ea17b7f60fb3f9d84e0b1555d56b46b39fb1/LICENSE) |

この一覧の元の記録は [docs/grammar-licenses.json](grammar-licenses.json) です。配布物には同梱しません。pack の版と許可するライセンスとの一致は、試験が確かめます。
