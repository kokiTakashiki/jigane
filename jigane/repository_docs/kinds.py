"""文書の種類の表。種類の名前、設定のキー、入る群と起動は、この表だけが持つ。"""

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum

from jigane.groups.definitions import GroupId


class DocumentKind(StrEnum):
    """リポジトリの文書の種類。値は実行の記録と標準エラーに出る。"""

    DEVELOPMENT = "development"
    SECURITY = "security"


class Launch(StrEnum):
    """文書が入りうる起動。束ねの役はどの種類も入らないので、ここに無い。"""

    POLICY_DIAGRAM = "policy_diagram"
    POLICY_DESIGN = "policy_design"
    POLICY_PRINCIPLE = "policy_principle"
    MICRO_DISCOVERER = "micro_discoverer"
    MICRO_VERIFIER = "micro_verifier"
    SECURITY_DISCOVERER = "security_discoverer"
    SECURITY_VERIFIER = "security_verifier"


@dataclass(frozen=True)
class DocumentKindSpec:
    kind: DocumentKind
    config_key: str
    heading: str
    launches: frozenset[Launch]


# 種類を足すときは、この表に行を足す。並びがプロンプトの節の順と記録の順になる
DOCUMENT_KINDS: tuple[DocumentKindSpec, ...] = (
    DocumentKindSpec(
        DocumentKind.DEVELOPMENT,
        "development_docs_path",
        "リポジトリの開発方針",
        frozenset(Launch),
    ),
    DocumentKindSpec(
        DocumentKind.SECURITY,
        "security_docs_path",
        "リポジトリのセキュリティ指針",
        frozenset({Launch.SECURITY_DISCOVERER, Launch.SECURITY_VERIFIER}),
    ),
)

GROUP_LAUNCHES: dict[GroupId, frozenset[Launch]] = {
    GroupId.MACRO: frozenset(
        {Launch.POLICY_DIAGRAM, Launch.POLICY_DESIGN, Launch.POLICY_PRINCIPLE}
    ),
    GroupId.MICRO: frozenset({Launch.MICRO_DISCOVERER, Launch.MICRO_VERIFIER}),
    GroupId.SECURITY: frozenset({Launch.SECURITY_DISCOVERER, Launch.SECURITY_VERIFIER}),
}


def kinds_for_groups(groups: Collection[GroupId]) -> tuple[DocumentKind, ...]:
    """指定した群のどれかの起動に入る種類を、表の順に返す。"""
    launches = frozenset().union(*(GROUP_LAUNCHES[group] for group in groups))
    return tuple(spec.kind for spec in DOCUMENT_KINDS if spec.launches & launches)
