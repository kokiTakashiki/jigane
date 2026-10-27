# jigane.regex

変更した正規表現を、LLM を使わずに数えて札にします。四つの方言（Python、Java、.NET、Swift）の共通部分を読み、節点の木にします。読めない書き方は木を返さず、読めなかった位置と理由を返します。

## 主な入口

| 名前 | 役割 |
| --- | --- |
| `syntax.parse` | 正規表現の本文と方言から、`RegexNode` か `Unreadable` を返します |
| `metrics.measure` | 構文木を一度歩いて、規則 R1〜R7 の元になる値（`RegexMetrics`）を数えます |
| `tags.judge` | 書き場所一つを構文解析と計測にかけ、閾値の表に当てた札の列か、読めなかった理由を `RegexVerdict` で返します。閾値と R6 の対象の呼び出しは `tags.py` の定数に置きます |
| `gate_tags.detect_tags` | 変更したファイルの書き場所を `tags.judge` にかけ、検出した札と、書式に合わない `allow-regex` の注記の違反を返します。`regex_entry` と `annotation_entry` が、ゲートの entry にします |
| `source.read_regex_source` | 木から正規表現の書き場所を読める言語のファイルを読み、アダプタと構文木の組（`RegexSource`）を返します。読めないファイルは `None` です |

## 他のモジュールとの関係

<!-- BEGIN GENERATED: dependencies (scripts/generate_readme_dependencies.py) -->

読むサブパッケージ：`gates`、`git`、`judgment_map`、`languages`

読まれるサブパッケージ：`flaky`、`skills`

<!-- END GENERATED: dependencies -->

- 方言の型 `RegexDialect` を `languages.base` から、所見の型と行の範囲を `gates` から、ファイルの読み取りを `git.tree_files` から、札の外し方を `judgment_map.allow_annotation` から読みます。

## 札の外し方

- 誤検知の札は、書き場所の行か直前の行に `jigane: allow-regex <規則> -- <理由>` を置くと外せます（例 `# jigane: allow-regex R3 -- 外部の仕様の形をそのまま写すため`）。置き場所と読み方は `jigane.judgment_map.allow_annotation` が持ち、`allow-flaky` と同じです。
- 外した札は消さず、gates.json の entry の `allowed` に理由を持たせます。終了コードには数えません。
- 理由が無い注記と R1〜R7 に無い規則の注記は、`RGX-ANNOTATION` の違反になります。
