# 言語を追加する

[README に戻る](../README.md)

プラグインのパッケージから言語を追加できます。パッケージを jigane と同じ環境に入れ（`uv tool install` で入れた jigane には `uv tool install jigane --with <パッケージ>`、`pipx` で入れた jigane には `pipx inject jigane <パッケージ>`、それ以外は `pip install` か `uv pip install`）、`jigane languages enable <名前>` で有効にします。

配布が複数あって言語名が重なるときは `--distribution <配布名>` で選びます。

`enable` は、プラグインのコードを読み込む前に、信頼の注意と配布の名前と版を示します。端末で y と答えたときだけ、コードを読み込んで検証し、データの置き場の `languages.json` に記録します。記録するのは、言語と配布の名前と版、承認した時点の配布の中身（RECORD に載る各ファイルの sha256）です。RECORD の無い配布は有効にできません。

起動のたびに、読み込む前に配布の中身を記録と照らします。版が違うときも、同じ版のままファイルが変わったり欠けたりしたときも、読み込みません。配布を更新したら、もう一度 `jigane languages enable <名前>` で確認を取ります。中身を記録する前の形の記録（要素に `files` が無いもの）は読めず、古い形式のエラーで止まります。

中身が変わったときは、変わった、欠けた、増えたファイルの相対パスを種類つきで、合わせて最大 5 件まで理由に示します（ハッシュの値は示しません）。`enable` の確認でも同じ理由を示します。

`jigane languages disable <名前>` で外します。

文法は、導入の後に `jigane languages fetch` で取得します。照合は「[文法の取得](data-destinations.md#文法の取得)」の節にあります。

`jigane languages list [--json]` は、言語ごとの出どころ、状態、拡張子、使える図と対応外の図を示します。`--json` の `interfaces` と表の「インターフェース」は、クラス名ではなく固定の識別子（`sequence`、`discriminator_flow_steps`、`string_literal_sets` など）で示します。クラスを改名しても識別子は変わりません。図は、アダプタが満たすインターフェース（`SequenceAdapter`、`DiscriminatorBranchExtractor` など）で決まります。判別の流れ図は `DiscriminatorBranchExtractor`（`discriminator_flow_steps`）、足した型と同じ項目を持つ型は `TypeFieldNameExtractor`（`type_field_names`）を実装した言語だけが対応し、足した公開のインターフェースと呼ぶ側は `AddedPublicNameFinder`（`added_public_names`）を実装した言語だけが対応します。有効にした言語が使えないと、`languages` の副コマンド以外は止まります。ただし、文法がまだ置き場に無いだけのときは止まらず、その言語を文法を取得していない言語として扱い（`languages list` の状態は `grammar_absent`）、名前を標準エラーに示します。文法を取得すれば使えます。

図の経路のインターフェースには、上のほかに、値の組を読む `StringLiteralSetExtractor`（`string_literal_sets`）、文字列のリテラルとの比較を読む `StringLiteralComparisonExtractor`（`string_literal_comparisons`）、ファイル直下の文字列定数を読む `TopLevelStringConstantExtractor`（`top_level_string_constants`）があります。値の組の表は、前の二つの両方を実装した言語だけが描けます。メソッドの形は `jigane/languages/base.py` にあります。外部のプラグインが実装したインターフェースは、0.1.8 までは jigane が使っておらず、効いていませんでした。0.1.9 から効きます。

作り方の例は `examples/jigane-language-kotlin/README.md` にあります。

追加の言語のコードと文法の共有ライブラリは、jigane と同じ権限で動きます。jigane は審査しません。出どころを確かめてから有効にします。詳しくは SECURITY.md の「追加の言語」を読んでください。

## インターフェースの識別子

`jigane languages list` の表と `--json` の `interfaces` は、次の識別子でインターフェースを示します。識別子は固定で、クラスを改名しても変わりません。

| インターフェース | 識別子 |
| --- | --- |
| `AddedPublicNameFinder` | `added_public_names` |
| `CommandArgumentExtractor` | `command_arguments` |
| `ConcurrencyAdapter` | `concurrency` |
| `DiscriminatorBranchExtractor` | `discriminator_flow_steps` |
| `FilePathExpressionExtractor` | `file_path_expressions` |
| `ImportedModuleExtractor` | `imported_modules` |
| `MarkupEventHandlerExtractor` | `markup_event_handlers` |
| `RegexSiteExtractor` | `regex_sites` |
| `SequenceAdapter` | `sequence` |
| `StoreAccessExtractor` | `store_accesses` |
| `StringLiteralComparisonExtractor` | `string_literal_comparisons` |
| `StringLiteralSetExtractor` | `string_literal_sets` |
| `TopLevelStringConstantExtractor` | `top_level_string_constants` |
| `TypeFieldNameExtractor` | `type_field_names` |
| `UnsupportedSyntaxDetector` | `unsupported_syntax` |

## サポートしない構文の申告

構文エラー（構文木のルートの誤り）は、アダプタの有無によらず、すべての言語に当たります。これに加えて、`UnsupportedSyntaxDetector` の `unsupported_syntax` を実装した言語は、サポートしない構文を申告できます。申告は任意です。実装しなければ、構文エラーだけが当たります。このインターフェースは試験的な段にあり、jigane のバージョンで形が変わりえます。

| 項目 | 規則 |
|---|---|
| メソッド | `unsupported_syntax(root_node, source, /) -> list[UnsupportedSyntax]`。サポートしない構文ごとに `UnsupportedSyntax(kind, line)` を一件返す |
| `line` | 1 以上、ファイルの行数以下の整数。構文の先頭の行 |
| `UnsupportedSyntaxKind.name` | 言語の中での種類の名前。`[a-z][a-z0-9_]*` |
| `UnsupportedSyntaxKind.title` | 見出し。「{見出し}です。」と読める名詞句 |
| `UnsupportedSyntaxKind.fix_hint` | 直し方。句点で終わる文 |
| 同じ `name` の種類 | 同じ `title` と `fix_hint` を持つ |
| 呼ばれない場合 | 構文エラーのファイルには呼ばれない |

言語名は `[a-z][a-z0-9]*` に合わせます。この規則に合わない名前の言語が実装すると、検出が `ValueError` で止まります。

種類の値は `{言語名}_{種類の name}`、警告の id は値を大文字にして `_` を `-` に置き換えた `UNREADABLE-{言語名}-{種類の name}` です。

| 言語 | 種類の name | 種類の値 | 警告の id |
|---|---|---|---|
| Swift | `operator_function` | `swift_operator_function` | `UNREADABLE-SWIFT-OPERATOR-FUNCTION` |
| 例：Kotlin の `foo` | `foo` | `kotlin_foo` | `UNREADABLE-KOTLIN-FOO` |

組み込みの Swift の実装（`jigane/languages/swift.py`）が最小の例です。演算子関数を種類として申告します。

```python
_OPERATOR_FUNCTION = UnsupportedSyntaxKind(
    "operator_function",
    "名前を取れない演算子関数（Swift）",
    "演算子関数の中身を名前のある関数に移し、演算子からその関数を呼んでください。",
)


class SwiftAdapter:
    def unsupported_syntax(self, root_node: Node, _source: bytes, /) -> list[UnsupportedSyntax]:
        return [
            UnsupportedSyntax(_OPERATOR_FUNCTION, node.start_point.row + 1)
            for node in operator_function_nodes(root_node)
        ]
```

案内と警告の出方は [commands.md](commands.md#読めない記述の案内) を読んでください。

## 言語プラグインのインターフェースの改名

0.1.9 で、言語プラグインのプラグイン API のバージョンを 2.0 に上げ、能力のインターフェース、メソッド、値の型、フィールドの名前を、取り出すものが分かる名前に変えました。旧名の別名は残していません。

プラグイン API のバージョンが 1 のプラグインは、この jigane では読み込めません。`languages` 以外の副コマンドはエラーで止まり、`jigane languages list` の理由の種類は `api_version_old` になります。止まったときは `jigane languages disable <名前>` で外せます。

直すには、次の表に従って名前を書き換え、`plugin()` の `api_version` に `ApiVersion(2, 0)` を書きます。`PLUGIN_API_VERSION` は `jigane.languages.api` から無くなったので、`ApiVersion` を import して、バージョンを自分で書いてください。インターフェースと値の型は、`jigane.languages.api.capabilities` から公開しています。`ValueSetReader` は二つに分かれたため、値の組と比較の両方を実装した言語だけが、値の組の表の対象になります。

### インターフェース

| 旧名 | 新名 |
| --- | --- |
| `DiscriminatorReader` | `DiscriminatorBranchExtractor` |
| `TypeFieldReader` | `TypeFieldNameExtractor` |
| `PublicNameReader` | `AddedPublicNameFinder` |
| `StoreAccessReader` | `StoreAccessExtractor` |
| `ValueSetReader` | `StringLiteralSetExtractor`、`StringLiteralComparisonExtractor`（二つに分かれました） |
| `StringConstantReader` | `TopLevelStringConstantExtractor` |
| `RegexAdapter` | `RegexSiteExtractor` |
| `FilePathAdapter` | `FilePathExpressionExtractor` |
| `CommandArgumentAdapter` | `CommandArgumentExtractor` |
| `ImportAdapter` | `ImportedModuleExtractor` |
| `MarkupBindingAdapter` | `MarkupEventHandlerExtractor` |

### メソッド

| 旧名 | 新名 |
| --- | --- |
| `discriminator_branches` | `discriminator_flow_steps` |
| `type_fields` | `type_field_names` |
| `value_sets` | `string_literal_sets` |
| `string_comparisons` | `string_literal_comparisons` |
| `string_constants` | `top_level_string_constants` |
| `regex_literals` | `regex_sites` |
| `file_path_patterns` | `file_path_expressions` |
| `imports` | `imported_modules` |
| `technology_of` | `technology_name_of` |
| `markup_path` | `paired_markup_path` |
| `markup_handlers` | `handler_names_in_markup` |
| `body_of` | `body_node_of` |
| `receiver_of` | `receiver_kind_of` |
| `imported_names` | `imported_name_origins` |

### 値の型

| 旧名 | 新名 |
| --- | --- |
| `Argument` | `CallArgument` |
| `Body` | `ConcurrencyBody` |
| `Self`、`Super`、`Name`、`Member`、`Call`、`Creation`、`Paren`、`Unknown` | `SelfReceiver`、`SuperReceiver`、`NameReceiver`、`MemberReceiver`、`CallReceiver`、`CreationReceiver`、`ParenReceiver`、`UnknownReceiver` |
| `Step`、`Return`、`BranchArm`、`Branch` | `FlowStep`、`FlowReturn`、`FlowBranchArm`、`FlowBranch` |
| `Import` | `ImportedModule` |
| `FilePathPattern` | `FilePathExpression` |
| `PublicName` | `AddedPublicName` |
| `RegexLiteral`、`RegexComposed`、`RegexScan` | `RegexLiteralSite`、`RegexComposedSite`、`RegexSites` |
| `ValueSet`、`StringComparison` | `StringLiteralSet`、`StringLiteralComparison` |
| `TypeFields` | `TypeFieldNames` |
| `StringConstant` | `TopLevelStringConstant` |

### フィールド

| 型 | 旧名 → 新名 |
| --- | --- |
| `CommandArgument` | `command` → `subcommand_name`、`names` → `option_names`、`dest` → `dest_text`、`default` → `default_text`、`help` → `help_text`、`action` → `action_text`、`type` → `type_text`、`choices` → `choices_text`、`group` → `group_variable_name`、`exclusive` → `group_exclusive` |
| `FilePathExpression`、`StoreSide` | `function` → `function_qualified_name` |
| `StoreSide` | `kind` → `format`、`types` → `created_type_names`、`labels` → `key_vocabulary_names` |
| `StoreAccess` | `names` → `file_names`、`pair_owner` → `pair_owner_type_name` |
| `RegexLiteralSite`、`RegexComposedSite` | `call` → `call_name` |
| `RegexSites` | `literals` → `literal_sites`、`composed` → `composed_sites` |
| `StringLiteralSet` | `name` → `binding_name`、`kind` → `definition_kind` |
| `StringLiteralComparison` | `value` → `literal_value`、`kind` → `comparison_kind`、`subject` → `compared_expression` |
| `TypeFieldNames` | `name` → `type_name`、`fields` → `field_names` |
| `CallSite`（フィールド）、`DeferredSite`（プロパティ） | `end` → `end_byte` |
