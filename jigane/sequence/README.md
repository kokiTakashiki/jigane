# jigane.sequence

レビューが変更した宣言から、変更が届く入口（根）ごとのシーケンス図を、LLM を使わずに作ります。tree-sitter で宣言の索引を作り、呼び出しの受け手の型を宣言の型注記から静的に解決し、根から変更した宣言へ至る経路を図のモデルにして、Mermaid に変換します。解決できない呼び出しは推測せず、未解決として図に残します。

対象は、言語アダプタが呼び出しの口（`SequenceAdapter`）を持つ言語で、いまは C#、Python、Swift、Java です。

## 主な入口

| 名前 | 役割 |
| --- | --- |
| `index.build_index` | 型とメンバを名前で引ける `Index` を作ります |
| `indexes.Indexes` | 言語アダプタごとの `Index` をまとめ、型の名前から引けるようにします |
| `resolve.extract_calls` | メンバの本体の呼び出しを評価の順に取り出し、受け手の型を解決します |
| `roots.changed_members` | 判断の地図の変更から、変更した宣言の集合を求めます |
| `roots.select_roots` | 変更した宣言のうち、他から呼ばれないものと、アダプタが入口と見なす名前（`main` など）のものを入口として選びます。テストのファイルからの呼び出しは、呼ばれる側を入口から外しません |
| `diagram.construct_diagrams` | 構築の入力（`DiagramInputs`）から、根ごとに、参加者と矢印の図と、図以外の項目（`DiagramSetNotes`）を持つ `DiagramSet` を作ります。逆引きと、どの図にも当たらない判断の箇所は、`ArrowHit`、`ParticipantHit`、`UnreachableRegion` の型で持ち、辞書にしません。識別子は知りません |
| `diagram_json.diagrams_to_dict` | 識別子付きの図の並び（`IdentifiedDiagram` の並び）と、図以外の項目（`DiagramSetNotes`）、図を作った木の側を受けて、識別子を載せた JSON の辞書にします。逆引きと判断の箇所の辞書への変換はここだけが持ちます。識別子は導かず、構築も呼びません |
| `review_diagrams.build_diagrams` | `DiagramInputs` から `construct_diagrams` で図を作り、`identities_of` で識別子を導いて `diagrams_to_dict` へ渡す、組み立ての入口です。変更前の木の図は関係の矢印を足しません |
| `before_tree.load_before_tree` | 変更前（基点）の木を読み、索引と変更した宣言、修飾名から引く本体を持つ宣言（`BeforeTree`）を作ります。基点の木が無いレビューでは `None` を返します。`before_diagrams` と `review_diagrams` を読みません |
| `before_diagrams.before_diagrams` | `BeforeTree` から、変更後の各図に対応する変更前の図か、無い理由（`BeforeResult`）を返します。`attach_before` が結果を図のモデルに持たせます。`before_tree`、`diagram`、`diagram_diff`、`diagram_json`、`identifiers`、`index`、`roots`、`side` を読み、`review_diagrams` は読みません。組み立ての根は `review_diagrams` が持ちます |
| `diagram_mermaid.to_mermaid` | 図のモデルを Mermaid の `sequenceDiagram` に変換します。`diagram_diff` を読みます |
| `diagram_select.select_diagrams` | 図のモデルから、根や判断の箇所で図を選びます。`region_ids` が図に載る判断の箇所の番号を引きます。描画（`diagram_mermaid`）と `diagram_diff` を読みません |
| `diagram_diff.mark_differences` | 変更前と変更後の矢印の列を突き合わせ、対比の印（`PlainMark`、`Retargeted`）を付けます。図のモデルの構築（`diagram`）を読みません |
| `side.Side` | 図や変更の集合をどちらの木から取るかを表します。一つの木を指す型（`TreeSide`）も持ちます |
| `identifiers.identities_of` | `DiagramSet` と木の側（`side.TreeSide`）から、図ごとに図と識別子（`DiagramIdentities`）を対にした `IdentifiedDiagram` の並びを返します。図の本体は `IdentifiedDiagram.diagram` の一か所だけが持ち、図以外の項目（`DiagramSetNotes`）は運びません。導出はここだけが持ちます。依存は `review_diagrams` → `diagram_json`、`identifiers`、`before_tree` と `before_diagrams`、`before_diagrams` → `before_tree`、`before_diagrams` → `diagram_json`、`before_diagrams` → `identifiers`、`diagram_json` → `identifiers` → `diagram` の一方向で、`diagram` は識別子を知りません |
| `model_json.model_to_json` | 図のモデルを JSON の辞書にします。対比の印の書き出しはここだけが持ちます |
| `review_diagrams.build_model` | レビューから図のモデルまでを通します。CLI の `jigane sequence` が使います |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`figures`、`git`、`judgment_map`、`languages`、`preconditions`、`reviews`

読まれるサブパッケージ：`commands`、`design`、`policy_materials`、`skills`

<!-- END GENERATED: dependencies -->

- `languages` は呼び出しの口を持つアダプタと構文木、テストのパス判定に、`git.tree_files` はツリーのファイルの読み取りに、`reviews.lifecycle` はレビューの内容に、`judgment_map` は変更の区間と分類に、`figures.models` は図の型に使います。`commands.failures` は `SequenceError` を処理します。

## Q&A

### なぜ言語の構文の知識を、各言語のアダプタだけが持つのですか

言語の構文の知識（型とメンバの節点、名前空間、入れ物を平らにする歩き）は各言語のアダプタだけが持ち、`index.py` はアダプタの `type_members` が返す型とメンバだけで索引を作り、構文の節点の型名を書きません。言語ごとにシーケンス図の実装を持つと、根の選び方や経路の切り詰めのような言語に依らない処理が言語の数だけ写され、一つの言語で直した不具合が他に残るためです。

### なぜ呼び出しの抽出の query を言語の層が持ち、シーケンス図の層は構文の節点の型名を書かないのですか

呼び出しの抽出に使う tree-sitter の query は言語の層（`jigane.languages`）が持ち、シーケンス図の層は構文の節点の型名を書かず、`receiver_of`、`locals_of`、`call_sites` の返す種別だけで解決します。言語の違いをアダプタの口に閉じ込めると、索引、解決、根の選び方、図のモデルを言語に依らない一つの実装にできるためです。

### なぜ言語ではなく、アダプタが `SequenceAdapter` を実装しているかで、図を描けるかを決めるのですか

`review_diagrams.build_model` は、レビューに呼び出しの口を持つ言語のファイルが無ければ `SequenceError` を投げ、`review_sequences` は同じ場合に図を空にして理由を注記として返し、口を持つかは、アダプタが `SequenceAdapter`（`type_members` を持つ Protocol）を実装しているかで決めます。シーケンス図は複数のオブジェクトのやり取りを示す図であり、言語は描く描かないの理由にならないので、描けないときは言語の名前でなく実際の理由を示すためです。

### なぜ `jigane sequence` と `jigane diagrams` は図の組み立てを共有するのですか

`jigane sequence` は一つの入口や判断の箇所に絞った図を出し、`jigane diagrams` は概要の画面に出す全体をまとめて出します。どちらも図の組み立ては `review_diagrams` を共有します。二つの副コマンドが同じ図を出すことを、組み立てを一つにすることで保つためです。

### なぜ図と矢印に識別子の欄があり、並びの位置から作らないのですか

図のモデルの JSON は、図に `id`、その矢印それぞれに `id` を持ちます。呼び出す側が SVG の矢印とモデルの矢印を並びの順で対応付けると、矢印の増減や並べ替えで対応がずれるため、中身から決まる値を欄にしました。値は SHA-256 の先頭 12 桁の 16 進数に接頭辞を付けた文字列です。図は `d_`、矢印は `a_` で始まります。

根の識別子 `root_id`（接頭辞 `r_`）は、図の直下の欄で、根の修飾名と、根の宣言の種類と仮引数のラベルと型の並び（同名で引数の違う多重定義を区別するシグネチャ）から決まり、側を含めません。変更前と変更後の図で同じ値になり、多重定義の根では別の値になります。図の `id` は、この `root_id` と、図を作った木の側（`before` か `after`）から決まります。矢印の `id` は、図の `id`、送り手と受け手の修飾名、呼び出しの名前、呼び出しの式のパスと行、同じ図の中で中身の同じ矢印の出現の順（0 始まり）から決まります。深さと状態は含めません。同じ入力から作り直すと同じ値になり、矢印の並びを入れ替えても各矢印の値は変わりません。矢印の識別子は呼び出しの行を材料に含めるので、呼び出しの行が動けばその矢印の値は変わります（作り直しで同じ値になる約束と並ぶ契約です）。変更前の図は同じ根の変更後の図と別の `id` を持ち、`before` の欄の下に入ります。

### なぜ差分のレビューとファイルのレビューで、索引にするファイルが違うのですか

差分のレビューは変更後のツリー全体を、ファイルのレビューは対象のファイルを索引にします。索引が図の参加者の候補を決め、その範囲がレビューの種類ごとに違うためです。差分のレビューは変更前と変更後の木、ファイルのレビューは選んだファイル群が範囲になります。

### なぜ変更に関わらない呼び出しだけの入口も図に出すのですか

変更した宣言へ届く呼び出しだけに畳むと矢印が 0 本になる入口（単独の関数、呼び先を解けない呼び出しを持つ入口）は、図にも注記にも名前が残らず、別のモジュールが内部の名前で呼んでいることが資料から落ちます。呼び出しの関係は構造なので、このような入口に限り、入口の直接の呼び出しを全て矢印にして描き直します。変更に関わらない呼び先は本体を辿らず、受け手の型を解けない呼び出しのうち、呼びの式の先頭の名前がそのファイルの import でリポジトリの中のモジュールを指すものだけを、ソースの呼びの式を名前にした「解けない呼び先」の参加者への破線の矢印にします。標準ライブラリ、外の依存、組み込みの呼び出しは、リポジトリの外のものなので描かず、従来どおり「変更に関わらない呼び出し」の件数に数えます。「変更に関わらない呼び出し」の注記は件数に加えて、リポジトリの中の呼び先の呼びの名前（重複は一つ）を 5 つまで並べ、超えた分は「ほか N 件」にします（例: `3 件: configure、sync（ほか外の呼び出し 1 件）`）。外の呼び出しは名前を並べず、件数だけを「ほか外の呼び出し」に残します。import を表に持たない言語（Swift、C#、Java）では、解けない呼び先の矢印は描きません。解けない呼び先の参加者は一枚の図に 5 つまでで、残りは件数の注記にします。呼び出しを一つも持たない入口だけが「図にしない」注記に残ります。変更前の図は変更した宣言へ届くかを比べるため、この矢印を足しません。

### なぜ一枚の図の参加者と矢印に上限があるのですか

一枚の図の参加者と矢印には上限があり、超えた分は描かずに注記します。上限を超える図は読めないため描かず、注記を残して、図がすべてを示していると読み手に誤解させないようにするためです。
