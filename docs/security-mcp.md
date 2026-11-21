# セキュリティの群の MCP の使い分け

[README に戻る](../README.md)

セキュリティレビューでは、`--mcps <名前>...` で基準資料を参照するサーバを選びます。省略すると MCP を使いません。選択したエージェントに合わせて、次のいずれかの方法でサーバを登録してください。

レビューの置き場と登録を分ける場合は、環境変数 `JIGANE_MCP_HOME` に登録用ディレクトリの絶対パスを指定します。そのディレクトリに `mcp-claude` または `mcp-codex` を置きます。省略時は `jigane debug paths` に出るデータの置き場を使います。`JIGANE_HOME` が指すレビューと実行記録の置き場は変わりません。macOS と Linux では、登録用ディレクトリも実行ユーザーの所有とし、ほかの利用者からの書き込みを禁止します。

## Claude Code に設定を渡す

登録用の置き場に `mcp-claude` ディレクトリを作り、その下に `<名前>.json` をサーバごとに用意します。名前に使える文字は半角英数字、ハイフン、下線です。ファイルの `mcpServers` には、ファイル名と同じ名前のサーバを一つ書きます。次は `mcp-claude/server-sec.json` の例です。実際の接続先は、利用する資料サーバの案内に従って設定してください。

```json
{
  "mcpServers": {
    "server-sec": {
      "type": "http",
      "url": "https://example.com/mcp"
    }
  }
}
```

以前の `mcp.json` を使っていた場合は、利用する登録を上の形に手元で移してください。登録が一つの場合は、名前を合わせてファイルを移せます。jigane は選んだファイルのパスとサーバ名を Claude Code に渡します。ファイルの読み込み、サーバの起動・接続、認証は Claude Code が行います。

Claude Code は、通常の起動で引き継いだ環境から、stdio の `env` や HTTP の認証ヘッダーに書いた MCP 用変数の `${VAR}` 参照を解決します。HTTP サーバの MCP OAuth 認証が保存済みなら、Claude Code が利用できます。[Claude Code の MCP 設定と認証](https://code.claude.com/docs/en/mcp)

設定はレビュー対象の作業ディレクトリの外に置いてください。macOS と Linux では、`mcp-claude` は所有者だけがアクセスできる権限（0700）、設定ファイルは所有者だけが読み書きできる権限（0600）にします。他の利用者が書き込める設定を検出すると、jigane は起動前に停止します。Windows では、利用者ごとのアクセス制御を使います。

Claude Code は選択名でサーバを絞り込みます。組織の MCP 許可リストはこの絞り込みに影響するため、各設定ファイルには対応する一つの登録だけを置いてください。[Claude Code の組織向け MCP 設定](https://code.claude.com/docs/en/managed-mcp)

Claude Code が承認なしで使う道具には、選んだサーバが公開する道具が含まれます。出所を確かめて、読み取り専用の基準資料を提供するサーバを選んでください。取得した資料や、道具に渡す問いは、[データの送り先](data-destinations.md)に記載しています。

見積もりとレビューには同じ名前を渡します。

```bash
jigane estimate payment security --mcps server-sec
jigane run payment security --mcps server-sec
jigane show payment security
```

Claude Code が接続や資料取得の失敗を報告すると、その起動の結果に表示します。CLI が接続状態を返さなかった場合は、状態を確認できなかった旨を表示します。サーバの応答形式は Claude Code が扱います。jigane のエラーには応答本文、認証情報、CLI の診断原文を含めません。

## Codex CLI に実行ファイルを渡す

macOS と Linux では、MCP のリソースを標準入出力（stdio）で提供し、引数なしで起動できる、信頼できる実行ファイルを登録できます。登録用の置き場に `mcp-codex` ディレクトリを作り、`<名前>` から実行ファイルの絶対パスにシンボリックリンクを張ります。登録と実行ファイルはレビュー対象の作業ディレクトリの外に置いてください。

```bash
ln -s /absolute/path/to/reference-server "<データの置き場>/mcp-codex/server-sec"
```

旧 `mcp.json` の登録を使っていた場合は、資料サーバの案内に従い、直接起動できる実行ファイルと、そのサーバ自身の設定・認証を用意してください。Codex CLI が登録した実行ファイルを起動します。jigane が渡すサーバ情報は名前と実行ファイルのパスです。起動引数やサーバ専用の環境変数を jigane に登録する操作はありません。サーバが認証を要する場合は、その実行ファイル自身が利用できる設定や認証手段を使います。Codex CLI 自体は保存済みの ChatGPT ログインでレビューします。HTTP 接続は Claude Code の設定で使えます。

Codex CLI は、選択したサーバの資料一覧、資料テンプレート、本文取得をモデルに提供します。サーバ固有のツールを公開する設定は無効です。CLI が選択したサーバの初期化や資料の取得に失敗したと報告すると、その起動の結果に表示します。

Codex では端末の `run` と `estimate` に同じ名前を渡します。非対話の `jigane debug run` は MCP の指定を受け付けません。

## 選択と本文取得を確認する

両方の CLI で、`jigane run` の結果と `jigane show <レビュー名> security` に、MCP の選択名と本文取得の記録が出ます。たとえば `mobile-sec` と `server-sec` を選び、`server-sec` だけで本文が確認された場合は次のように表示します。

```text
MCP: 選択 mobile-sec, server-sec、本文取得の記録 server-sec
```

`--json` では `run.mcp_servers` が選択名、`run.mcp_read_servers` が本文取得を確認できた選択名です。MCP を選んで実行し、確認できた取得が 0 件なら、テキストでは「なし」、JSON では空の配列です。本文取得を計測していない記録では、選択名があれば「未計測」と表示し、JSON では `mcp_read_servers` を省きます。

本文取得は、CLI の標準の資料取得が完了し、結果に中身のある資料が確認できた場合に記録します。接続、一覧、URI の紹介、空の資料は件数に含みません。Claude Code では、サーバ固有の道具が返す資料が通常のテキストと同じ形式に変換されるため、その道具の結果からは本文取得を判定できません。シャードの一部が失敗した場合も、確認済みの取得は表示します。

## 資料を使い分ける

`run` と `estimate` のセキュリティの群で `--mcp-guide <パス>` を指定すると、サーバごとの使い分けをプロンプトに加えます。TOML の最上位は `mcps` の表、各表は空でない一行の `use_for` だけを持ちます。名前の集合は `--mcps` と一致させてください。

```toml
[mcps.mobile-sec]
use_for = "iOS と Android のアプリのコード"

[mcps.server-sec]
use_for = "サーバ、API、サーバ上で動くプラグインのコード"
```
