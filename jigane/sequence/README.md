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
| `diagram.build_diagrams` | 根ごとに、参加者と矢印の図のモデルを作ります |
| `diagram.to_mermaid` | 図のモデルを Mermaid の `sequenceDiagram` に変換します |
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

### なぜ差分レビュー式と機能レビュー式で、索引にするファイルが違うのですか

差分レビュー式は変更後のツリー全体を、機能レビュー式は対象のファイルを索引にします。索引が図の参加者の候補を決め、その範囲がレビューの式ごとに違うためです。差分は変更前と変更後の木、機能は選んだファイル群が範囲になります。

### なぜ一枚の図の参加者と矢印に上限があるのですか

一枚の図の参加者と矢印には上限があり、超えた分は描かずに注記します。上限を超える図は読めないため描かず、注記を残して、図がすべてを示していると読み手に誤解させないようにするためです。
