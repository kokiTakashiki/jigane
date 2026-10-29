# jigane.languages

ソースファイルを tree-sitter で構文木にし、言語ごとの違いを `LanguageAdapter` の裏に閉じ込めます。パスを受け取ってアダプタを返し、構文木と元のバイト列を受け取って宣言の一覧（修飾名、種別、可視性、行の範囲）を返します。アダプタは構文の事実だけを持ちます。何を判断の箇所とするか、何を flaky とするかは持ちません。

## 主な入口

| 名前 | 役割 |
| --- | --- |
| `base.LanguageAdapter` | 言語ごとの Protocol を定め、名前、文法、拡張子、テストとビルドのパターン、`declarations` を持ちます |
| `base.Declaration` | 一つの宣言を表します。`span`、`signature`、`body` を行の範囲で持ちます |
| `base.Visibility` | 可視性の三段（`FILE`、`INTERNAL`、`PUBLIC`）を表します |
| `base.parse` | アダプタの文法でソースを構文木にします |
| `base.comment_nodes`、`base.leaves_and_comments`、`base.import_rows` | 構文木からコメントの節点、葉とコメントの節点、import の文が占める行を取り出します。コメントと import の判定は、呼ぶ側が行頭の見かけで行わず、これを使います |
| `base.ImportAdapter`、`base.Import` | import の文からモジュールの名前と行を取り出す口（`imports`）と、技術の単位への束ね方（`technology_of`）を定めます。`jigane.technology_list` が使います |
| `base.FilePathAdapter`、`base.FilePathPattern`、`base.FileUsage` | 書く、または読むファイルの名前の組み立て（`open` の第一引数、`write_text` などの受け手、`Path(...)` と `/` の式）を原文のまま取り出す口（`file_path_patterns`）を定めます。Python のアダプタだけが持ち、他の言語は口を持ちません（未対応と該当なしを分けます）。`jigane.skills.policy_file_paths` が使います |
| `base.StoreAccessReader`、`base.StoreAccess`、`base.StoreSide`、`base.StoreRole` | 関数ごとの保存物の書く側と読む側（役割は `StoreRole` の値で持ち、`StoreAccess` は空でない側の組を持ちます。鍵、作る型、鍵の語彙の名前、ファイル名）を、複数ファイルにまたがって取り出す口（`store_accesses`）を定めます。定数と列挙型の解決、import の追跡は口の側が行います。Python のアダプタだけが持ち（実装は `python_stores`）、他の言語は口を持ちません。`jigane.skills.policy_stores` が使い、突き合わせと束ねは言語に依らず向こうで行います |
| `registry.adapter_for` | パスの拡張子から、登録済みのアダプタを引きます。無ければ `None` を返します |
| `registry.is_test_path`、`registry.is_build_path` | 全アダプタのパターンでテストとビルドの定義を判定します |
| `python.PythonAdapter`、`java.JavaAdapter`、`swift.SwiftAdapter`、`csharp.CSharpAdapter` | 各言語のアダプタの実装を持ちます |
| `calls_core`、`concurrency_core`、言語別の `*_concurrency.py` | 呼び出しの抽出、受け手の判別、局所変数の走査と、並行処理の図の抽出の、言語に依らない骨組みを `calls_core` と `concurrency_core` が持ちます。言語ごとの節点の表と判定は、`python_concurrency`、`java_concurrency`、`csharp_concurrency`、`swift_concurrency` が持ちます |
| 言語別の `*_regex.py`、`regex_locals` | 正規表現の書き場所を構文木だけから読みます（`python_regex`、`java_regex`、`csharp_regex`、`swift_regex`）。引数が局所の識別子のときの代入の追跡は、四つの言語で共通の `regex_locals` が持ちます |
| `grammars`、`fetch_main` | tree-sitter の文法を固定の置き場に取得し、読み込む前に sha256 を記録と照合します。`fetch_main` は、依存の導入のあとに文法を取得する入口（`python -m jigane.languages.fetch_main fetch`）です |
| `plugin`、`plugins`、`data_places` | 外の言語の契約（`LanguagePlugin`、`ApiVersion`）と、その読み込みと、言語の資料（照会と flaky の型）の置き場を持ちます。外のパッケージが import する窓口は `api` です |
| `type_names`、`csharp_xaml`、`python_arguments`、`python_file_paths`、`python_walk` | アダプタが組み合わせて使う部品です。型注記の原文から単純な型の名前を取り出し（`type_names`）、XAML のイベント結線から分離コードのメソッドの名前を読み（`csharp_xaml`）、argparse の副コマンドと引数（`python_arguments`）とファイル名の組み立て（`python_file_paths`）を構文木から読みます。`python_walk` は、Python の構文木を読むモジュールが共有する足場です |
| `bridges/*.toml` | 言語ごとの橋渡しの表です。どの技術からどの技術へつなぐかを、見つけ方（`via`）とあわせて持ち、`technology_list.bridges` が読みます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`preconditions`、`storage`

読まれるサブパッケージ：`commands`、`design`、`flaky`、`judgment_map`、`llm_runs`、`policy_materials`、`regex`、`sequence`、`skills`、`static_analysis`、`technology_list`

<!-- END GENERATED: dependencies -->

- `preconditions.required` で、構文木の節点が欠けていないことを表明します。
- `judgment_map.classification` は変更を宣言に割り当て、`policy_materials.changed_files` と `skills.policy_repeats` は方針のレビューの材料に、`flaky` はテストファイルの判定とコメントの節点の型に、`sequence` はシーケンス図の索引に、`technology_list` は import の口に、`regex` は方言の型に読みます。

## Q&A

### 新しい言語を足す

新しい言語を足すときの決まりです。

#### なぜアダプタのプロパティはすべて読み取り専用なのですか

`jigane/languages/<言語>.py` に `LanguageAdapter` を満たすクラスを置き、プロパティはすべて読み取り専用にして、`declarations` は `Declaration` を返します。可変属性で宣言すると不変（invariant）になり、固定長のタプルを持つクラス変数が `tuple[str, ...]` に適合しなくなるためです。

#### なぜ `_plugins` の並びが拡張子の勝ち負けを決めるのですか

`registry.py` の `_plugins` に `LanguagePlugin` を加え、拡張子が重なるときは先に並べたアダプタが勝ちます。`adapter_for` が並びの順に拡張子を調べ、最初に合ったアダプタを返すためです。

#### なぜ `grammar` にはパックの中の名前を書くのですか

文法は `tree_sitter_language_pack` が持つ名前を `grammar` に書きます。パーサも言語もその名前でパックから引くので、パックに無い名前は解決できないためです。

#### なぜ Makefile や CI の定義だけは `registry` が持つのですか

テストとビルドの判定は、アダプタの `test_directory_names`、`test_file_patterns`、`build_file_patterns` に書きます。言語に属さない Makefile や CI の定義は `registry.COMMON_BUILD_FILE_PATTERNS` と `COMMON_BUILD_PATH_PATTERNS` が持ち、アダプタの表だけでは、これらの変更が判定から落ちるためです。

#### なぜ `using (...)` は import に含めないのですか

コメントと import の判定は、アダプタの `comment_node_types` と `import_node_types`（コメントの節点の型、import や using の文の節点の型）に書き、`using (...)` のような破棄の構文は import に含めません。`using` 文は名前を取り込む構文ではなく、破棄の構文であるためです。

#### なぜ `INTERNAL` を使う言語と使わない言語があるのですか

`INTERNAL` は、同じモジュールの中でだけ見える修飾子を持つ言語（Java、C#、Swift）だけが使い、持たない言語は `PUBLIC` と `FILE` の二段にします。`INTERNAL` は「ファイルの外から見えるが、モジュールの外へは出ない」段であり、その修飾子の無い言語には当てはめる先が無いためです。

#### なぜ本体が無い宣言とシグネチャと同じ行で始まる宣言の `body` は `None` なのですか

本体の無い宣言と、本体がシグネチャと同じ行で始まる宣言では、`body` を `None` にします。前者は宣言の全体が、後者は本体の開き括弧がある行までがシグネチャの範囲になり、本体として残る行が無いためです。

### 守るテスト

言語を足したときに、あわせて足すテストと資料の決まりです。

#### なぜ言語ごとに `tests/test_languages_<言語>.py` を足すのですか

`tests/test_languages_<言語>.py` が、宣言の列挙、可視性、行の範囲を守ります。新しい言語にも同じ形のファイルを足します。公開の判定、修飾名、シグネチャと本体の分け方が言語ごとに異なるため、言語ごとに表を持つアダプタを別々に守る必要があるためです。

#### なぜ import の判定は `tests/test_languages_syntax_rows.py` で言語ごとに守るのですか

`tests/test_languages_syntax_rows.py` が、コメントの節点の収集と、各アダプタの import の行の判定を守ります。新しい言語にも import の判定を足します。Python の複数行の import、Java の `package` の除外、C# の `using` 文の除外、Swift の属性付きの import のように、import の節点の扱いが言語ごとに異なるためです。

#### なぜ `tests/test_languages_registry.py` が要るのですか

`tests/test_languages_registry.py` が、拡張子の引き方とテスト、ビルドのパス判定を守ります。これらはアダプタ単体ではなく、並びの順に調べる `adapter_for` と、アダプタのパターンに共通のパターンを合わせる `is_test_path`、`is_build_path` が決めるためです。

#### なぜシーケンス図の query は `queries/<文法名>.toml` に置くのですか

シーケンス図が使う呼び出しの抽出の tree-sitter の query は、`queries/<文法名>.toml` に置き、`queries.load_queries` が名前から引きます。現在は `csharp.toml`、`java.toml`、`python.toml`、`swift.toml` が、いずれも`calls`、`lambdas`、`method_groups` を持ちます。Java の `method_groups` は `Foo::bar` の形のメソッド参照を、`lambdas` はラムダに加えて無名クラスと局所クラスの本体（`class_body`）を拾います。言語の層は `jigane.sequence` に依存しません。

#### なぜ flaky の query のファイル名はアダプタの `name` に合わせるのですか

flaky の検出は、アダプタの `name` を `queries/<言語>.toml` のファイル名と対応させます。言語を足したら `jigane.flaky` の README の手順も行います。検出は query の言語がアダプタの `name` と等しいものだけを適用し、アダプタに無い言語の queries ファイルは `load_catalog` が `CatalogError` にするためです。

## 外の言語を書く

外のパッケージから言語を足すプラグインの書き方です。動く例は `examples/jigane-language-kotlin/` にあり、その README が手順の全体を持ちます。

- `pyproject.toml` の `[project.entry-points."jigane.languages"]` に、言語名を名前、`パッケージ:plugin` を値とする項目を置きます。値は引数の無い関数です。
- `plugin()` は `jigane.languages.api.LanguagePlugin` を返します。`origin` は `importlib.metadata.distribution(<配布名>)` から `Distribution(名前, 版)` を作ります。`api_version` は `PLUGIN_API_VERSION` を渡します。
- プラグインが import するのは公開の窓口 `jigane.languages.api` だけにします。jigane の内部のモジュールには依存しません。窓口の核（`LanguageAdapter`、宣言の値の型、`span_of`、`split_declaration`、`BodyBounds`、`DeclarationSpans`、`LanguagePlugin`、`GrammarSpec`、出どころ、`ApiVersion`、`PLUGIN_API_VERSION`）は、版の互換を約束します。
- 窓口の Protocol のメソッドの引数は、すべて位置専用（`/`）です。jigane は引数を位置で渡し、名前では渡しません。実装の引数の名前は自由で、使わない引数は頭に `_` を付けてかまいません。
- 宣言を組み立てるときは、行の範囲を自前で計算せず `span_of` と `split_declaration` を使います。`split_declaration` は節点から `span`、`signature`、`body` を一度に返し、本体を一つの節点で持つ言語は、その節点を `BodyBounds` の開きと閉じの両方に渡します。本体の無い宣言には `None` を渡します。
- `GrammarSpec` の `pack_version` は tree-sitter-language-pack の版、`hashes` は platform の名前（`macos-arm64`、`linux-x86_64` など）から共有ライブラリの sha256 への対応です。ハッシュは、パックのリリースの `parsers-<platform>.tar.zst` をマニフェストの sha256 と照合してから展開し、取り出した共有ライブラリ（`libtree_sitter_<文法名>`）から取ります。パックの版を上げたら取り直します。組み込みの文法の記録（`grammar-hashes.json`）も一つの版の値なので、`pyproject.toml` はパックを `==` で一つの版に固定し、版を上げるときは固定と記録を同じ変更で直します。文法はパックの中の名前だけを指せます。
- 照会と flaky の型の資料は、`LanguagePlugin.data` の置き場に `languages/queries/<文法名>.toml` と `flaky/queries/<言語名>.toml` で置きます。本体と同じ名前のファイルを置くと、その外の言語ごと使えません。
- 宣言の一覧以外の図に使う口の Protocol は `jigane.languages.api.capabilities` にあります。試験的な段で、版の約束の外にあり、jigane の版で形が変わることがあります。口を持たない言語は、その図に対応しません。
- 契約の版は `PLUGIN_API_VERSION`（`ApiVersion(major, minor)`）です。核の型の削除と変更は major を、追加は minor を上げます。jigane は、プラグインの宣言した版の major が等しく、minor が jigane の minor 以下のときに読み込みます。
