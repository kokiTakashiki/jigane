# jigane.languages.api

プラグインが import してよい公開の窓口です。定義は `jigane.languages.base` と `jigane.languages.plugin` にあり、ここは再公開だけを行います。

## 主な入口

| 名前 | 役割 |
| --- | --- |
| `jigane.languages.api` | 核です。`LanguageAdapter`、宣言の値の型、`LanguagePlugin`、`GrammarSpec`、出どころ（`Builtin`、`Distribution`）、`PLUGIN_API_VERSION` が入ります |
| `jigane.languages.api.capabilities` | 試験的な段です。口の Protocol と、それらが使う値の型が入ります。版の約束の外にあり、jigane の版で形が変わることがあります |

窓口の Protocol のメソッドの引数は、すべて位置専用（`/`）です。jigane は引数を位置で渡し、名前では渡しません。実装の引数の名前は自由で、使わない引数は頭に `_` を付けてかまいません。
