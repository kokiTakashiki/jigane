# 貢献の方針

## Issue と Pull Request の扱い

このリポジトリは Issue だけを受け付け、Pull Request は受け付けません。届いた Pull Request は、読まずに閉じます。修正は持ち主が行うので、起きたことと期待を Issue に書いてください。

## 不具合と要望

不具合の報告と機能の要望は、Issue の雛形から書いてください。雛形の項目を埋めると、再現と判断が早く進みます。

## 脆弱性

脆弱性は Issue に書かないでください。[SECURITY.md](SECURITY.md) の窓口（Private vulnerability reporting）で報告してください。

## 開発の環境

開発に使う入口は、利用者向けの `jigane` のコマンドとは別に、macOS と Linux が make、Windows が just です。

取得は `git clone https://github.com/kokiTakashiki/jigane` で行い、取得したディレクトリで `make setup`（Windows は `just setup`）を走らせます。

```bash
make setup
```

```bash
just setup
```

make と just は、どちらも同じ作業（`setup`、`jigane`、`lint`、`fmt`、`test`、`check`）を持ちます。macOS と Linux では `make setup` で環境を整え、Windows では `just setup` で環境を整えます。引数は、make では `ARGS="..."` で渡し（`make jigane ARGS="new files src --name x"` など）、just では後ろに続けます（`just jigane new files src --name x` など）。どちらの入口も uv が要り、無ければ導入の案内を出します。

試験は `JIGANE_HOME` を一時のディレクトリへ向けないと始まる前に止まります（本物の置き場を書き換えないための見張りです）。`make test` と `just test` は一時のディレクトリを作って向けるので、`pytest` を直接走らせるときは自分で `JIGANE_HOME` を向けてください。

`make check`（Windows は `just check`）は、lint と test が緑で終わったときだけ、作業木が綺麗な HEAD の SHA を印として `.git` の中（worktree ごと）に書きます。未コミットの変更が残っていれば印を書かずに失敗します。`make setup` が `core.hooksPath` を `.githooks` に向け、push の前の `pre-push` が、送る各 ref の先端のコミットを印と照らして、ゲートを通っていないコミットの push を止めます。先端の木はゲートで確かめてあるので、途中のコミットは照らしません。ブランチの削除は照らしません。緊急のときは `git push --no-verify` でこの確かめを飛ばせます。

```bash
make jigane ARGS="new pr <PR の URL>"
```

```bash
just jigane new pr <PR の URL>
```

`setup` は tree-sitter の文法を、仮想環境の中（`<sys.prefix>/share/jigane/grammars/`）へ取得します（実行時には取得しません）。

ほかのリポジトリをレビューするときは、そのリポジトリのディレクトリで、jigane の Makefile（Windows は justfile）を指して呼びます。`jigane` は呼び出した場所で走ります。

```bash
make -f <作業ツリー>/Makefile jigane ARGS="new range <基点>..<先端> --name <名前>"
```

```bash
just --justfile <作業ツリー>/justfile jigane new range <基点>..<先端> --name <名前>
```

Windows の justfile の PowerShell の手順（道具の版の確かめと案内）は、Windows の環境で走らせて確かめていません。

README の「既知の限界」の節は、その限界を直した件で、その項目を節から消します。

同じ層の兄弟のサブパッケージが、別の兄弟を新しく import するときは、`pyproject.toml` の `[tool.jigane.allowed_sibling_imports]` の import する側の行へ相手を足し、`make generate`（Windows は `just generate`）を走らせます。`[tool.importlinter]` の兄弟の forbidden 契約は、この表と layers 契約から `scripts/generate_import_contracts.py` が作るので、印の間を手で直さないでください。`make lint` は最新かどうかも見ます。サブパッケージを足したときは、layers 契約のどれかの層へ入れて同じ生成を走らせます。`jigane/README.md` のサブパッケージの表と層の図も、この生成が layers 契約と各サブパッケージの README の先頭の一文から作り直すので、手で直さないでください（`make lint` が最新かも見ます）。各サブパッケージの README の依存の名前の一覧（「他のモジュールとの関係」の印の間）も、`scripts/generate_readme_dependencies.py` が実際の import から作るので、手で直さないでください。印の外の説明の文は手で書き、一覧と食い違わないようにします。

開発中の jigane を PATH の `jigane` として使うには、リポジトリの根で `uv tool install --editable .` を走らせます。編集した内容は入れ直さずに反映されます。外すときは `uv tool uninstall jigane` を走らせます。

## ライセンス

このリポジトリのライセンスは MIT です。
