# jigane.static_analysis

静的検査の結果（SARIF）を読み、検証して、正規化した指摘にします。判断の地図、保存、表示は受け持ちません。

## 主な入口

| 名前 | 役割 |
| --- | --- |
| `reader.read_sarif` | SARIF のファイルの一覧と、解析の根、リポジトリの根から、`SarifImport` を返します。上限を超えた入力、通常でないファイル、JSON として読めないファイル、型に合わないファイルは `SarifError` で止めます |
| `reader.SarifLimits` | ファイルの個数、大きさ、指摘の件数、文字列の長さの上限です。既定は `DEFAULT_LIMITS` で、試験は小さい値に差し替えます |
| `paths.resolve_uri` | 指摘の URI を、リポジトリの根からの posix の相対パスか、捨てる理由（`DropReason`）にします |
| `models.StaticFinding` | 正規化した指摘一件です。識別子は渡したファイル、run、結果の順に `S1`、`S2` と振ります |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`git`、`judgment_map`、`languages`、`storage`

読まれるサブパッケージ：`commands`、`reviews`

<!-- END GENERATED: dependencies -->

- 文字列から制御文字と端末のエスケープ列を除く `sanitize_text` を `languages.plugins` から読みます。

## 読み込みの決まり

- 使う欄だけを型に持ち、知らない欄は読み捨てます。使う欄の型が違えば、ファイル全体を拒みます。
- 位置（URI と開始行）を持たない結果と、2 番目以降の位置は使いません。位置のない結果と、パスを解決できない結果は捨てて、`SarifImport.dropped` に理由ごとの件数を持ちます。
- `revisionId` はファイルごとに書かれたまま持ち、コミットとの照合はしません。
