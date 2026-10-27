# jigane.technology_list

レビューが変更したファイルの import から、使っている技術の表を LLM を使わずに作ります。import の文を言語アダプタで取り出し、技術の単位に束ね、同じリポジトリの中で定義されたモジュールか、言語と OS のものかに分類し、変更後と変更前の使われ方を数えます。

外部のパッケージの分類と依存の宣言の変化は `manifests` が担います。橋渡しは `bridges` が、言語ごとの表（`languages/bridges/*.toml`）の見つけ方に当たる箇所として挙げます。

## 主な入口

| 名前 | 役割 |
| --- | --- |
| `build.build_model` | レビューから技術選定のモデル（dict）を作ります。CLI の `jigane technology-list` が使います |
| `materials.short_section_text` | 一回の形の「技術選定」の節を、変化のあった技術だけの表にして、`build_model` から Markdown で作ります。`jigane.skills.macro` が使います |
| `repository_modules.collect` | ツリーの中で定義されたモジュールの名前を、根拠のファイルと行とともに集めます |
| `repository_modules.match` | 技術の名前が、集めた定義のどれに当たるかを返します |
| `manifests.read` | 木を一度走査し、依存の宣言（パッケージの名前と版、ファイルと行）と、変更したファイルに効くマニフェストの配備の下限を読みます。ファイル名ごとの知識は、一行に集めた表 `MANIFEST_KINDS` が持ちます |
| `manifests.match` | 技術の名前が、宣言のパッケージのどれに当たるかを返します |
| `manifests.changes` | 変更前と変更後の宣言を比べ、足した、消した、版を変えたものを返します |
| `platforms.model` | 配備の下限を変更の前後で比べ、変化を付けて一覧にします |
| `tables` | 節ごとの「ヘッダと行（文字列のセル）」を作ります。CLI は桁そろえ、資料は Markdown の表にするだけにします |
| `changes` | 変化の値（added など）と、資料と CLI が共有する表示の文言の表を持ちます |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`git`、`languages`、`markdown`、`reviews`

読まれるサブパッケージ：`commands`、`skills`

<!-- END GENERATED: dependencies -->

- `languages` から import の口と束ね方を、`git.tree_files` からツリーのファイルの列挙と読み取りを、`markdown.cells` から表のセルを、`reviews.lifecycle` からレビューの内容を読みます。

## Q&A

### なぜツリーのファイルは `jigane.git.tree_files` の入口だけで読むのですか

ツリーのファイルは `jigane.git.tree_files` の入口だけで読みます。シンボリックリンクと木の外への参照を、その入口が一か所で扱っているためです。

### なぜモデルの分類は先に当たったもので決めるのですか

モデルの分類は `same_repository`、`external`、`language_os` の三つで、先に当たったもので決めます。`language_os` は同じリポジトリにも外部にも当たらなかったものを指すので、当たりを調べる順序が分類の定義の一部になるためです。

### なぜ `manifest_changes` は変更前のツリーが無いとき None なのですか

`manifest_changes` は変更前のツリーが無いとき None です。変更前のツリーが無いと依存の宣言を比べられず、変わらなかった場合と区別して示す必要があるためです。
