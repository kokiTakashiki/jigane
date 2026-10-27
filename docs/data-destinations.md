# データの送り先

jigane が外へ送る相手の一覧です。ここに無い相手へ、jigane が自分で接続することはありません。

## 送り先

| ホスト | 送るもの | 時機 | 口 |
| --- | --- | --- | --- |
| `github.com` | gh と git の取得の要求（ref と commit の SHA）と、gh と git の認証。文法の配布物の取得の要求 | `jigane new pr`、`jigane refresh`（gh と git）。`jigane languages fetch`（文法） | gh、git、tree-sitter-language-pack |
| `api.github.com` | リポジトリと PR の番号、gh の認証 | `jigane new pr`、`jigane refresh` | gh |
| `release-assets.githubusercontent.com` | 文法の配布物の取得の要求 | `jigane languages fetch` | tree-sitter-language-pack |
| （claude を通して） | プロンプト（差分、PR の題と本文、ボットのコメント、図と表、前の回の指摘）、claude が読んだファイルの中身、モデルの出力。宛先は claude が決める | `jigane run` の各起動 | 起動する外部コマンド claude |
| （利用者が選ぶ MCP サーバ） | モデルがその MCP サーバの道具に渡す問い。宛先は利用者の `mcp.json` の設定が決める | `jigane run security --mcps <名前>` を指定したときだけ | claude が呼ぶ MCP サーバ |

ホストの列が括弧で始まる行は、ホスト名を持ちません。送り先が jigane の外（claude の設定や利用者の MCP の設定）で決まるためです。

gh と git が `github.com` へ送るのは取得の要求と認証で、レビュー対象のコードは受け取るだけです。gh が呼ぶコマンドは README の「jigane が呼ぶ gh のコマンド」にあります。gh は `GH_HOST=github.com` に固定され、`GH_REPO` は渡されません。

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
