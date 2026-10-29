# jigane-language-kotlin

jigane に外のパッケージから言語を足す例です。Kotlin の宣言（クラス、object、interface、関数、プロパティ）の一覧だけを提供します。シーケンス図などの宣言の一覧以外の図には対応しません。

## 構成

- `pyproject.toml`：entry point の群 `jigane.languages` に、名前 `kotlin` で `jigane_language_kotlin:plugin` を登録します。
- `jigane_language_kotlin/__init__.py`：`plugin()` が `LanguagePlugin` を返します。出どころは `importlib.metadata` で自分の配布の名前と版から作ります。アダプタは `jigane.languages.api` の `LanguageAdapter` を満たします。
- `tests/test_kotlin_example.py`：アダプタの試験です。

プラグインが import するのは、公開の窓口 `jigane.languages.api` だけです。jigane の内部のモジュールには依存しません。

## 例で扱わないこと

この例は、公開の窓口 `jigane.languages.api` の安定した口だけを使う形を示すため、次のものを持ちません。

- 技術の表（`jigane technology-list`）：import の口（`ImportAdapter` の `imports` と `technology_of`）を実装していないので、Kotlin のファイルの import を読みません。Kotlin のファイルだけを変えた差分では、import が無いと出ます。この口は試験的な段（`jigane.languages.api.capabilities`）にあり、jigane の版ごとに形が変わりえます。
- flaky の検査（`jigane flaky`）：`LanguagePlugin.data` が `None` で、型の置き場（`flaky/queries/kotlin.toml`）を持たないので、Kotlin のテストに当てる型がありません。テストのパスの判定（`test` と `androidTest`）は通りますが、検出はしません。
- シーケンス図などの宣言の一覧以外の図：それぞれの口を実装していません。

足すときは、`jigane/languages/README.md` の外の言語の節にある口と資料の置き場に従います。

## 使い方

jigane と同じ Python の環境に入れます。

```sh
uv pip install ./examples/jigane-language-kotlin
```

入れただけでは使えません。利用者が配布名と言語名と版、配布の中身（RECORD に載るファイルの sha256）を承認して初めて、entry point が読み込まれます。配布を更新したり中身が変わったりしたら、承認し直すまで読み込まれません。

## 文法のハッシュ

`GrammarSpec.hashes` に、tree-sitter-language-pack の Kotlin の共有ライブラリ（`libtree_sitter_kotlin`）の sha256 を platform ごとに記録します。記録したのは、パックが配る六つの platform（`linux-aarch64`、`linux-x86_64`、`macos-arm64`、`macos-x86_64`、`windows-aarch64`、`windows-x86_64`）で、パックの版は 1.22.1 です。

パックの版を上げたら、リリースの `parsers-<platform>.tar.zst` を、マニフェストの sha256 と照合してから展開し、取り出した共有ライブラリのハッシュで取り直してください。`scripts/generate_grammar_hashes.py` が、組み込みの記録とこの宣言を同じ配布物から作り直します。

## 既知の限界

パック 1.22.1 の Kotlin の文法は、書き方によって次のように解析を誤ります。

- 本体を持つトップレベルの `object Name { ... }` を、ファイルの先頭や別の宣言の直後に一行で書くと、`object_declaration` ではなく式（`infix_expression`）として解析され、宣言として拾えません。import の後に本体を複数行で書くと、`object_declaration` として解析され、拾えます。
- 本体の無いメソッドを持つ interface を `interface Api { fun call() }` のように一行で書くと、木に `ERROR` の節点が入ります。複数行で書くと誤りになりません。アダプタは `ERROR` の中も辿って拾います。

## 試験

```sh
uv run pytest examples/jigane-language-kotlin/tests
```

Kotlin の文法が要ります。環境変数 `TREE_SITTER_LANGUAGE_PACK_CACHE_DIR` を一時の置き場に向けると、暗黙の取得がそこへ入ります。
