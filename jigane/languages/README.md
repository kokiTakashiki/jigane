# jigane.languages

ソースファイルを tree-sitter で構文木にし、言語ごとの違いを `LanguageAdapter` の裏に閉じ込めます。パスを受け取ってアダプタを返し、構文木と元のバイト列を受け取って宣言の一覧（修飾名、種別、可視性、行の範囲）を返します。アダプタは構文の事実だけを持ちます。何を判断の箇所とするか、何を flaky とするかは持ちません。

## 主な入口

| 名前 | 役割 |
| --- | --- |
| `base.LanguageAdapter` | 言語ごとの Protocol を定め、名前、文法、拡張子、テストとビルドのパターン、`declarations` を持ちます |
| `base.Declaration` | 一つの宣言を表します。`span`、`signature`、`body` を行の範囲で持ちます |
| `base.Visibility` | 可視性の三段（`FILE`、`INTERNAL`、`PUBLIC`）を表します |
| `base.parse` | アダプタの文法でソースを構文木にします |
| `python_ast.read_python`、`python_ast.parse_python` | Python のファイルを標準ライブラリの `ast` で読む入口です。構文の誤りと、入れ子の深い式による `RecursionError`、`MemoryError`、`ValueError` は、構文木の作成と呼ぶ側の収集の両方でここが受け、読めないファイルは `None` で返します。Python を読む各モジュールと `jigane.design.recommend` が、読めないファイルを飛ばす処理をここに任せます |
| `base.StringConstantReader`、`base.StringConstant`、`python_constants.string_constants` | ファイル直下で空でない文字列を代入している定数の名前、値、代入の文の最初の行を取り出す口（`string_constants`）を定めます。口を持つのは Python のアダプタだけで、他の言語は口を持ちません。Python の実装（`python_constants`）は、モジュール直下（最上位の `if` と `try` の内側を含む）の代入（`NAME = "x"`、型注釈つき、`A = B = "x"`、括弧や行の継続をまたぐ代入）を構文木から取り出します。関数やクラスの中、f 文字列、文字列でない値は拾わず、読めないファイルは空です。`jigane.design` が副コマンドの名前と保存物の定数の引き当てに使い、口を持たない言語のファイルは空として扱います |
| `base.comment_nodes`、`base.leaves_and_comments`、`base.import_rows` | 構文木からコメントの節点、葉とコメントの節点、import の文が占める行を取り出します。コメントと import の判定は、呼ぶ側が行頭の見かけで行わず、これを使います |
| `base.ImportAdapter`、`base.Import` | import の文からモジュールの名前と行を取り出す口（`imports`）と、技術の単位への束ね方（`technology_of`）を定めます。`jigane.technology_list` が使います |
| `base.FilePathAdapter`、`base.FilePathPattern`、`base.FileUsage` | 書く、または読むファイルの名前の組み立て（`open` の第一引数、`write_text` などの受け手、`toml.load(path)` のような外の道具へパスを渡す呼び出し、`Path(...)` と `/` の式）を原文のまま取り出す口（`file_path_patterns`）を定めます。Python、Swift、Java、C# のアダプタが持ちます（Swift は `URL`、`FileManager.default`、`Data` と `String` の初期化子、`write(to:)` を、Java は `Files` のメソッド、`FileReader` などのコンストラクタ、`Path.of` や `resolve` の連なりを、C# は `File` と `Directory` のメソッド、`StreamReader` と `StreamWriter` のコンストラクタ、補間文字列か `+` を含む `Path.Combine` と `Path.Join` を読みます）。`jigane.skills.policy_file_paths` が使います |
| `base.PublicNameReader`、`base.PublicName`、`base.PublicNameKind` | 変更で足した、または引数を足した公開の名前（関数、メソッド、クラス、列挙の値、引数）と、変更の後の木の中の呼ぶ側を取り出す口（`added_public_names`）を定めます。変更の前後の木（相対パスからソースへの対応）と変更したファイルのパスを受け取り、名前の種類、定義の場所、呼ぶ側の関数を持つ `PublicName` を返します。Python（実装は `python_public_names`）、Swift（実装は `swift_public_names`）、C#（実装は `csharp_public_names`）、Java（実装は `java_public_names`）のアダプタが持ちます。Python、Swift、Java、C# の四つは、言語に依らない部分（定義の索引、前後の比べ、呼ぶ側の数え方）を `public_names_core` に任せ、構文木の歩き方と言語固有の規則だけを持ちます。C# は `private` と `file` の修飾を持つ名前を非公開とし、修飾のないメンバは非公開、`interface` と `enum` のメンバは公開として読みます。メソッド、コンストラクタ、`enum` の列挙子と `const` を対象にし、メソッドグループと `using static` で受けた列挙の値は呼ぶ側に数えません。Swift は `private` と `fileprivate` の修飾を持つ名前を非公開とし、引数は呼ぶ側が渡すラベルで照合します（外部名が `_` の引数は拾いません）。`extension` のメンバは、拡張される型の名前を付けて数えます。Java は `private` を非公開とし（`interface` のメンバは修飾なしで公開）、引数は足さず、コンストラクタは `型.型` で数え、`型.値` のメンバ参照だけを定数の呼ぶ側にします（`switch` の裸の定数とメソッド参照は数えません）。`jigane.skills.policy_callers` が使い、行の組み立ては言語に依らず向こうで行います |
| `base.StoreAccessReader`、`base.StoreAccess`、`base.StoreSide`、`base.StoreRole` | 関数ごとの保存物の書く側と読む側（役割は `StoreRole` の値で、形式は `StoreFormat`（JSON、CSV、文字列を組んで書く独自の形式）で持ち、`StoreAccess` は空でない側の組を持ちます。鍵、作る型、鍵の語彙の名前、ファイル名）を、複数ファイルにまたがって取り出す口（`store_accesses`）を定めます。定数と列挙型の解決、import の追跡は口の側が行います。ファイル名が関数の引数で渡る保存物は「関数名 の引数 引数名」の名前で関数ごとに束ね、独自の形式は書く側と読む側の関数だけを持って鍵は持ちません。Python（実装は `python_stores`）と Swift（実装は `swift_stores`。いまは `UserDefaults.standard` と、Codable の JSON（`JSONEncoder` の `encode` と `JSONDecoder` の `decode`。「型 の JSON」の名前で束ね、鍵は型の格納プロパティ）と、Keychain（`SecItemAdd`、`SecItemUpdate`、`SecItemDelete` が書く側、`SecItemCopyMatching` が読む側。「Keychain」の一つの名前で束ね、鍵は問い合わせの辞書の `kSecAttrAccount`）の書く側と読む側だけ）のアダプタが持ち、他の言語は口を持ちません。`jigane.skills.policy_stores` が使い、突き合わせと束ねは言語に依らず向こうで行います |
| `base.DiscriminatorReader` | `SequenceAdapter` の口に加えて、関数の本体の分岐と return を、言語に依らない形で文書順に取り出す口（`discriminator_branches`）を定めます。Python、Java（`java_discriminators`）、Swift（`swift_discriminators`）、C#（`csharp_discriminators`）のアダプタが持ちます。`jigane.policy_materials.discriminators` が使います |
| `base.TypeFieldReader`、`base.TypeFields` | トップレベルの型（Python のクラス、Swift の `struct`、`class`、`actor`、`enum`、Java の `class`、`record`、`enum`、C# の `class`、`struct`、`record`、`enum`）の名前、場所、宣言した項目の名前を、ファイルごとに取り出す口（`type_fields`）を定めます。項目は Python がクラス本体の型注記と列挙の値（`python_type_fields`）、Swift が格納プロパティと `enum` の case（`swift_type_fields`。計算プロパティと `static` は除く）、Java がインスタンスのフィールド、`record` の成分、`enum` の定数（`java_type_fields`。`static` のフィールドは除く）、C# がインスタンスのフィールド、自動実装プロパティ、`record` の位置引数、列挙子（`csharp_type_fields`。`static`、`const`、本体を持つプロパティ、`event` は除く）です。読めないファイルは空を返します。`jigane.skills.policy_field_copies` が使います |
| `base.ValueSetReader`、`base.ValueSet`、`base.StringComparison` | 要素がすべて短い文字列のリテラルで、要素が三つ以上の並び（tuple、set、frozenset、list、列挙型の値）を、ファイルごとに取り出す口（`value_sets`）と、文字列のリテラルとの比較（等値、`in`、`startswith` と `endswith`、正規表現の一致。比べている相手の式の原文 `subject` は Python だけが埋め、他の言語は空）を取り出す口（`string_comparisons`）を定めます。値の組は Python（`python_value_sets`）、Java（`java_value_sets`）、C#（`csharp_value_sets`）、Swift（`swift_value_sets`）が読み、拾う形は各モジュールの docstring に書きます。比較は Python（`python_string_comparisons`）、Java（`java_string_comparisons`）、C#（`csharp_string_comparisons`）、Swift（`swift_string_comparisons`）が読みます。値の組には、名前に束ねた定義か名前の無いリテラルかの区別（`ValueSet.kind`、`DefinitionKind`）を持たせ、四言語は、宣言の名前を取り出せたときに名前に束ねた定義、名前が取れず宣言にも代入にも含まれないと構文木から確かめられたときに名前の無いリテラル、それ以外（宣言や代入の中にあるが名前が取れない並び）を未決とします（既定は未決で、プラグインは手直し無しで動きます）。短い文字列の上限と要素の最小の数は `value_set_core` が一か所で持ちます。`jigane.policy_materials.value_sets` が使い、珍しい値の絞り込みと重なる組のまとめは言語に依らず向こうで行います |
| `registry.adapter_for` | パスの拡張子から、登録済みのアダプタを引きます。無ければ `None` を返します |
| `registry.language_for_suffix` | パスの拡張子から、選べる言語の状態（`LanguageReady` か `LanguageGrammarAbsent`）を引きます。当たらない、有効にしていない、使えない（`LanguageUnavailable`）言語は `None` を返します。`states()` を読むだけで、文法の確認は呼び直しません。本体の内側の関数で、プラグインには出しません |
| `registry.is_test_path`、`registry.is_build_path` | 全アダプタのパターンでテストとビルドの定義を判定します |
| `csharp_nodes` | C# の型の宣言の節点の表（`TYPE_KINDS`）と、文字列リテラルの節点の組（`STRING_LITERALS`。通常、逐語、raw で、補間文字列は含まない。`csharp_file_paths`、`csharp_regex`、`jigane.flaky.checks` が引く）を正本として持ち、列挙型と delegate を除いた集合（`TYPE_DECLARATIONS`）を導きます。名前空間と `#if` を貫く宣言の走査（`flatten`、`namespace_container`）も持ちます。修飾子の集合（`modifiers`）と、そこから可視性を決める `declared_visibility`（`private` と `file` は `FILE`、`public` と `protected` は `PUBLIC`、`internal` は `INTERNAL`、どれも無ければ呼び出し側の既定）も持ちます。呼び出せるものの芯（`CALLABLE_BODIES`。メソッド、コンストラクタ、ローカル関数、アクセサ）とラムダの組（`LAMBDAS`）も持ち、`csharp_concurrency` はラムダを足し、`csharp_regex` はそのまま、`csharp_file_paths` はローカル関数を除いてデストラクタを足して使います。呼び出しの節点 `INVOCATION` と計算した値とみなす式の組 `COMPUTED_EXPRESSIONS` も持ち、`jigane.flaky.checks` が引きます。`csharp`、`csharp_concurrency`、`csharp_value_sets`、`csharp_type_fields` がここから読みます |
| `discriminators_core` | Swift、Java、C# の判別の部品が共有する、言語に依らない部分です。言語ごとの差し込みを `Rules` にまとめ、文の並びを `Return` と `Branch` に振り分ける骨格（`steps`）、`if` の連なり（`if_branch`）、`switch` の分岐（`switch_branch`）、throw を値なしの `Return` にする扱い、`return` の値の絞り込み（`member_text`）を持ちます。本体の無い `case` を次の腕に束ねるかは `Rules` で切り替えます |
| `csharp_discriminators` | C# のメンバーの本体の分岐と return を、構文木だけから読む部品です（文の振り分けと分岐の組み立ては `discriminators_core` に任せ、`CSharpAdapter.discriminator_branches` が委譲します）。`if`、`switch` 文、`return`、`throw` と、`return` の式または式本体のメンバの `switch` 式を読み、ループ、`try`、`using`、`lock`、ローカル関数とラムダの本体、`yield return`、`?:` は読みません。`return` の値は `Kind.A` の形のメンバ参照だけを残します |
| `java_discriminators` | Java のメソッドの本体の分岐と return を、構文木だけから読む部品です（文の振り分けと分岐の組み立ては `discriminators_core` に任せ、`JavaAdapter.discriminator_branches` が委譲します）。`if`、`switch` 文、`return`、`yield`、`throw` と、`return` と `yield` の `switch` 式を読み、ループ、`try`、ラムダの本体は読みません。`return` の値は `Kind.A` の形のフィールド参照だけを残します |
| `swift_discriminators` | Swift の関数の本体の分岐と return を、構文木だけから読む部品です（`SwiftAdapter.discriminator_branches` が委譲します）。文の振り分けと分岐の組み立ては `discriminators_core` に任せ、`if`、`guard`、`switch`、`return`、`throw` と、`return` の式または本体が一文だけの式としての `if` と `switch` を読みます。戻り値の型が分かれば、`.foo` を `Owner.foo` に直します。`return` の値は `.foo` と `Kind.foo` のメンバ参照だけを残します |
| `python_public_names` | Python の足した公開の名前（関数、メソッド、クラス、列挙の値、引数）と呼ぶ側を、`ast` だけから読む部品です（`PythonAdapter.added_public_names` が委譲します）。`ast` の歩き方（定義、呼び出し、属性と名前の出現の拾い方）と、足した引数を呼ぶ側が渡しているかの照合（キーワード、または位置）だけを持ち、前後の比べと呼ぶ側の数え方は `public_names_core` に任せます。メソッドは属性の呼び出しだけを、クラスは呼び出しと属性の参照も呼ぶ側に数えます。足した引数とクラスに足した `__init__` の引数は、引数ごとの規則の結果（`AddedParameter`）として返します |
| `csharp_public_names` | C# の足した公開の名前と呼ぶ側を、構文木だけから読む部品です（`CSharpAdapter.added_public_names` が委譲します）。構文木の歩き方（可視性、定義、呼び出しの拾い方）だけを持ち、前後の比べと呼ぶ側の数え方は `public_names_core` に任せます |
| `csharp_type_fields` | C# の型の項目の名前を、構文木だけから読む部品です（`CSharpAdapter.type_fields` が委譲します）。トップレベルの型（ファイルの直下、名前空間、`#if` の中）を読み、入れ子の型は読みません。構文木を作れないファイルは、そのファイルだけ捨てます |
| `python.PythonAdapter`、`java.JavaAdapter`、`swift.SwiftAdapter`、`csharp.CSharpAdapter` | 各言語のアダプタの実装を持ちます |
| `calls_core`、`concurrency_core`、言語別の `*_concurrency.py` | 呼び出しの抽出、受け手の判別、局所変数の走査と、並行処理の図の抽出の、言語に依らない骨組みを `calls_core` と `concurrency_core` が持ちます。言語ごとの節点の表と判定は、`python_concurrency`、`java_concurrency`、`csharp_concurrency`、`swift_concurrency` が持ちます |
| `node_text` | tree-sitter の節点の文字列を取る補助です。`text_of` は text が無い節点を不正として止め、`text_or_empty` は節点が None のときも text が無いときも空文字列を返します。言語の部品と flaky の検査は、同名の補助を持たずここから使います |
| `java_nodes` | Java の型の宣言の節点の表（`TYPE_KINDS`、`TYPE_NODES`、注釈の型を除いた `TYPE_NODES_WITHOUT_ANNOTATION`）とメソッド相当の節点 `METHOD_NODES` の正本、メソッドの呼び出しの節点 `METHOD_INVOCATION` と計算した値とみなす式の組 `COMPUTED_EXPRESSIONS`（`jigane.flaky.checks` が引く）、修飾子から可視性を決める `declared_visibility`（`private` は `FILE`、`public` と `protected` は `PUBLIC`、どちらも無ければ呼び出し側の既定）を持ちます。`java`、`java_concurrency`、`java_file_paths`、`java_public_names`、`java_type_fields` が引きます |
| `swift_nodes` | Swift の型の宣言の節点 `TYPE_DECLARATIONS`（`class_declaration` と `protocol_declaration`。`extension` も `class_declaration` で表される）、型の宣言と `extension` を見分けるキーワード `TYPE_KEYWORDS`、本体を持つ関数と初期化子の節点 `DECLARED_FUNCTION_NODES`、一行、複数行、raw の三種の文字列リテラルの節点 `STRING_LITERALS`（`jigane.flaky.checks` も引く）、一行の文字列リテラルの本文を読む `string_literal` を持ちます。`swift`、`swift_concurrency`、`swift_public_names`、`swift_type_fields`、`swift_regex`、`swift_stores`、`swift_store_common`、`swift_store_lexicon`、`swift_value_sets`、`swift_string_comparisons`、`swift_file_paths` が引きます |
| `java_type_fields` | Java の型の項目の名前を、構文木だけから `TypeFields` として取り出します。入口の `type_fields_of` は構文木を作る関数を受け、作成と走査の `RecursionError`、`MemoryError`、`ValueError` だけを捕まえて、そのファイルを空で返します |
| `java_public_names` | 変更で足した公開の名前（型、メソッド、コンストラクタ、定数）と、変更の後の木の中の呼ぶ側を、構文木だけから `PublicName` として取り出します。入口の `added_public_names_of` は構文木を作る関数を受けます。構文木の歩き方（可視性、定義、呼び出しの拾い方）だけを持ち、関数の本体の中の宣言は定義にしません。前後の比べと呼ぶ側の数え方は `public_names_core` に任せます |
| `public_names_core` | Python、Swift、Java、C# の公開の名前の部品が共有する、言語に依らない部分です。定義（`Definition`）と呼ぶ側（`Site`）と、それを集める木（`Tree`）を持ち、定義の索引は（種類、修飾名）をキーにして、同じ修飾名の定義をファイルとオーバーロードをまたいで一つに合わせます。構文木の型には依らず（Swift、Java、C# は tree-sitter の節点、Python は `ast`）、`Rules` が構文木の型を受けます。足した名前を選ぶ側（`added_definitions`）と、定義ごとに呼ぶ側を数える側（`sites_of`、`callers_of`）を別の関数に分けます。構文木を作れないファイルは、そのファイルだけ捨てます。コンストラクタ、メソッド、型の使われ方を拾う表、定義の索引のキー、足した引数の規則（`added_parameters`、渡さなければ呼びません）は言語ごとに違うので、構文木を作る関数と歩き方とともに一つの設定（`Rules`）にまとめます。足した引数の規則は、言語固有の結果（引数の名前と、その引数を渡す呼ぶ側を持つ `AddedParameter`）だけを返し、`PublicName` は core が一か所で組みます。`added_public_names` は前後の木と変えたファイルに加えて `Rules` だけを受け、下の関数へ渡します（`Tree` は持ちません）。歩き方は定義の並びを集めるだけで、索引への登録は `parse_all` が `Rules` のキーで行い、四つが同じ一本の流れを通ります |
| 言語別の `*_regex.py`、`regex_locals` | 正規表現の書き場所を構文木だけから読みます（`python_regex`、`java_regex`、`csharp_regex`、`swift_regex`）。引数が局所の識別子のときの代入の追跡は、四つの言語で共通の `regex_locals` が持ちます |
| `grammars`、`fetch_main` | tree-sitter の文法を固定の置き場に取得し、読み込む前に sha256 を記録と照合します。`fetch_main` は、依存の導入のあとに文法を取得する入口（`python -m jigane.languages.fetch_main fetch`）です |
| `plugin`、`plugins`、`approval`、`enabled`、`validation`、`states`、`data_places` | 追加の言語の契約（`LanguagePlugin`、`ApiVersion`）と、その読み込みと、言語の状態の型（`states`。`LanguageReady` など四つの状態と、その和 `LanguageState`、理由の種類 `ReasonKind`。`plugins` を import しません）と、言語の資料（照会と flaky の型）の置き場を持ちます。読み込みは責務ごとに分けます。`approval` は内容の承認（`ApprovedContent`、RECORD から中身を数える `read_content`、承認し直しの理由 `reapproval_reason`、配布の今の中身を承認の記録と照らす `content_error`、sha256 の 16 進の書式の判定 `is_sha256_hex`）、`enabled` は有効化の記録 `languages.json` の保存と読み込み（`EnabledLanguage`、`load_enabled`、`save_enabled`。`EnabledLanguage` は承認の中身を受け取って作り、配布を読む窓口を持ちません。配布名は `text.distribution_names` の `normalize_distribution` で正規化して持ちます）、`validation` は一つのプラグインを単独で判定する `validate_plugin`、`plugins` は entry point の探索 `discover` を持ちます。`plugins` が `approval`、`enabled`、`validation`、`states` を import し、逆向きの import は置きません。プラグインのパッケージが import する窓口は `api` です |
| `file_paths_core` | Swift、Java、C# のファイルの名前の部品が共有する、言語に依らない部分です。報告した式の範囲の管理、読み書きの呼び出しの後に組み立ての式を拾う順序、連なりの最も外だけを報告する規則、変数の一段の追跡、囲む関数の修飾名を持ちます。呼び出しからパスを渡す引数と使い道の組を返す関数、名前を組む式と文字列を組む式の判定、節点の名前の組、宣言から初期値を取る関数は言語ごとに違うので、一つの設定（`Rules`）にまとめます。`file_path_patterns` は節点とパスに加えて `Rules` だけを受け、下の関数へ渡します（`Rules` は型に持たせません）。深すぎて辿れないファイルは、そのファイルだけ捨てて空を返します |
| `type_names`、`csharp_xaml`、`python_arguments`、`python_file_paths`、`swift_file_paths`、`swift_stores`、`swift_store_common`、`swift_store_lexicon`、`swift_store_userdefaults`、`swift_store_codable`、`swift_store_keychain`、`java_file_paths`、`csharp_file_paths`、`python_walk` | アダプタが組み合わせて使う部品です。型注記の原文から単純な型の名前を取り出し（`type_names`）、XAML のイベント結線から分離コードのメソッドの名前を読み（`csharp_xaml`）、argparse の副コマンドと引数（`python_arguments`）とファイル名の組み立て（`python_file_paths`、`swift_file_paths`、`java_file_paths`、`csharp_file_paths`）を構文木から読みます。Swift、Java、C# の三つは、言語に依らない部分を `file_paths_core` に任せ、呼び出しの分解と語彙だけを持ちます。`python_walk` は、Python の構文木を読むモジュールが共有する足場です。Swift の保存物は `swift_stores` が入口で、ファイルの読み込み、関数の列挙、道の振り分け、`StoreAccess` の組み立てだけを持ちます。道は `swift_store_userdefaults`（`UserDefaults.standard`）と `swift_store_codable`（呼び出しの検出、値の型の解決、触れ方の組み立ての三段）と `swift_store_keychain`（呼び出しの検出、問い合わせの辞書の探索、鍵を解いた触れ方の組み立ての三段）が持ち、鍵の語彙、Codable の型の項目、道へ渡す語彙 `Lexicon` は `swift_store_lexicon`、触れ方 `Touch` と構文木の小さな道具は `swift_store_common` が持ちます。道のモジュールは `swift_stores` を import しません |
| `bridges/*.toml` | 言語ごとの橋渡しの表です。どの技術からどの技術へつなぐかを、見つけ方（`via`）とあわせて持ち、`technology_list.bridges` が読みます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`preconditions`、`storage`、`text`

読まれるサブパッケージ：`commands`、`design`、`flaky`、`judgment_map`、`llm_runs`、`policy_materials`、`regex`、`sequence`、`skills`、`technology_list`

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

## 追加の言語を書く

プラグインのパッケージから言語を追加するプラグインの書き方です。動く例は `examples/jigane-language-kotlin/` にあり、その README が手順の全体を持ちます。

- `pyproject.toml` の `[project.entry-points."jigane.languages"]` に、言語名を名前、`パッケージ:plugin` を値とする項目を置きます。値は引数の無い関数です。
- `plugin()` は `jigane.languages.api.LanguagePlugin` を返します。`origin` は `importlib.metadata.distribution(<配布名>)` から `Distribution(名前, 版)` を作ります。`api_version` は `PLUGIN_API_VERSION` を渡します。
- プラグインが import するのは公開の窓口 `jigane.languages.api` だけにします。jigane の内部のモジュールには依存しません。窓口の核（`LanguageAdapter`、宣言の値の型、`span_of`、`split_declaration`、`BodyBounds`、`DeclarationSpans`、`LanguagePlugin`、`GrammarSpec`、出どころ、`ApiVersion`、`PLUGIN_API_VERSION`）は、版の互換を約束します。
- 窓口の Protocol のメソッドの引数は、すべて位置専用（`/`）です。jigane は引数を位置で渡し、名前では渡しません。実装の引数の名前は自由で、使わない引数は頭に `_` を付けてかまいません。
- 宣言を組み立てるときは、行の範囲を自前で計算せず `span_of` と `split_declaration` を使います。`split_declaration` は節点から `span`、`signature`、`body` を一度に返し、本体を一つの節点で持つ言語は、その節点を `BodyBounds` の開きと閉じの両方に渡します。本体の無い宣言には `None` を渡します。
- `GrammarSpec` の `pack_version` は tree-sitter-language-pack の版、`hashes` は platform の名前（`macos-arm64`、`linux-x86_64` など）から共有ライブラリの sha256 への対応です。ハッシュは、パックのリリースの `parsers-<platform>.tar.zst` をマニフェストの sha256 と照合してから展開し、取り出した共有ライブラリ（`libtree_sitter_<文法名>`）から取ります。パックの版を上げたら取り直します。組み込みの文法の記録（`grammar-hashes.json`）も一つの版の値なので、`pyproject.toml` はパックを `==` で一つの版に固定し、版を上げるときは固定と記録を同じ変更で直します。文法はパックの中の名前だけを指せます。
- 照会と flaky の型の資料は、`LanguagePlugin.data` の置き場に `languages/queries/<文法名>.toml` と `flaky/queries/<言語名>.toml` で置きます。本体と同じ名前のファイルを置くと、その追加の言語ごと使えません。
- 宣言の一覧以外の図に使う口の Protocol は `jigane.languages.api.capabilities` にあります。試験的な段で、版の約束の外にあり、jigane の版で形が変わることがあります。口を持たない言語は、その図に対応しません。
- 契約の版は `PLUGIN_API_VERSION`（`ApiVersion(major, minor)`）です。核の型の削除と変更は major を、追加は minor を上げます。jigane は、プラグインの宣言した版の major が等しく、minor が jigane の minor 以下のときに読み込みます。
