"""能力のインターフェースの固定の識別子。

`jigane languages list` の表と `--json` の `interfaces` は、Protocol のクラス名ではなく
このキーを出す。クラスを改名してもキーは変えない。Protocol にクラス属性を足すと
runtime_checkable の isinstance が壊れるので、対応は Protocol を鍵にした辞書で持つ。
"""

from collections.abc import Mapping

from jigane.languages.base import (
    AddedPublicNameFinder,
    CommandArgumentExtractor,
    ConcurrencyAdapter,
    DiscriminatorBranchExtractor,
    FilePathExpressionExtractor,
    ImportedModuleExtractor,
    MarkupEventHandlerExtractor,
    RegexSiteExtractor,
    SequenceAdapter,
    StoreAccessExtractor,
    StringLiteralComparisonExtractor,
    StringLiteralSetExtractor,
    TopLevelStringConstantExtractor,
    TypeFieldNameExtractor,
    UnsupportedSyntaxDetector,
)

INTERFACE_KEYS: Mapping[type, str] = {
    AddedPublicNameFinder: "added_public_names",
    CommandArgumentExtractor: "command_arguments",
    ConcurrencyAdapter: "concurrency",
    DiscriminatorBranchExtractor: "discriminator_flow_steps",
    FilePathExpressionExtractor: "file_path_expressions",
    ImportedModuleExtractor: "imported_modules",
    MarkupEventHandlerExtractor: "markup_event_handlers",
    RegexSiteExtractor: "regex_sites",
    SequenceAdapter: "sequence",
    StoreAccessExtractor: "store_accesses",
    StringLiteralComparisonExtractor: "string_literal_comparisons",
    StringLiteralSetExtractor: "string_literal_sets",
    TopLevelStringConstantExtractor: "top_level_string_constants",
    TypeFieldNameExtractor: "type_field_names",
    UnsupportedSyntaxDetector: "unsupported_syntax",
}
