"""UnsupportedSyntaxDetector と二つの型の公開の段の試験。"""

from jigane.languages import api, base
from jigane.languages.api import capabilities


def test_the_experimental_tier_exports_the_detector_and_its_two_types() -> None:
    for name in ("UnsupportedSyntaxDetector", "UnsupportedSyntax", "UnsupportedSyntaxKind"):
        assert getattr(capabilities, name) is getattr(base, name)
        assert name in capabilities.__all__


def test_the_core_tier_does_not_export_the_detector() -> None:
    assert not hasattr(api, "UnsupportedSyntaxDetector")


def test_the_experimental_tier_exports_the_string_extractors_and_their_value_types() -> None:
    names = (
        "StringLiteralSetExtractor",
        "StringLiteralComparisonExtractor",
        "TopLevelStringConstantExtractor",
        "StringLiteralSet",
        "StringLiteralComparison",
        "TopLevelStringConstant",
        "DefinitionKind",
        "ComparisonKind",
    )
    for name in names:
        assert getattr(capabilities, name) is getattr(base, name)
        assert name in capabilities.__all__
        assert not hasattr(api, name)
