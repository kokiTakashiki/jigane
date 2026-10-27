"""変更した正規表現の札を、ゲート違反の entry にするための選り分け。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from jigane.gates.entry import Active, Allowed, GateEntry, GateSeverity, GateState, RegexDetail
from jigane.gates.explanation import GateExplanation
from jigane.gates.line_scope import LineScope
from jigane.judgment_map.allow_annotation import AllowedSpot, scan_allow_comments, split_rows
from jigane.languages.base import RegexLiteral
from jigane.regex.source import read_regex_source
from jigane.regex.tags import CountedRegex, RegexTag, Rule, Severity, explain, judge

EXCERPT_LIMIT = 200
REGEX_ANNOTATION_ID = "RGX-ANNOTATION"

# 注記の違反は規則の定義を持たないので、文はここで持つ
ANNOTATION_EXPLANATION = GateExplanation(
    "allow-regex の注記が書式に合わないと、どの規則を何の理由で外したのかを機械が読めません。",
    "注記に規則の名前か理由が無い、または知らない規則を指しています。",
    "注記を allow-regex <規則> -- <理由> の形に直してください。",
)


@dataclass(frozen=True)
class RegexDetection:
    """札一つ分の検出。literal は書き場所、tag はそこに付いた札。

    state は、注記で外した札なら Allowed(理由)、外していなければ Active。
    """

    literal: RegexLiteral
    tag: RegexTag
    state: GateState = field(default_factory=Active)


@dataclass(frozen=True)
class AnnotationViolation:
    """書式に合わない allow-regex の注記。"""

    path: str
    line: int
    excerpt: str


@dataclass(frozen=True)
class RegexTagFindings:
    """検出した札と、書式に合わない注記の違反。"""

    detections: tuple[RegexDetection, ...]
    violations: tuple[AnnotationViolation, ...]


def _literals(root: Path, path: str) -> tuple[RegexLiteral, ...]:
    source = read_regex_source(root, path)
    return () if source is None else source.scan(path).literals


def detect_tags(
    root: Path,
    paths: Sequence[str],
    renames: Mapping[str, str],
    base_root: Path | None,
    scope: LineScope,
) -> RegexTagFindings:
    """変更した正規表現の札。差分のレビューは、変更前の木の同じファイルに無い本文だけ。

    移したファイルは、renames（変更後のパスから変更前のパス）の旧パスの本文と比べる。読めなかった正規表現は札を持たないので落とす。
    書式に合わない注記の違反は scope の行のものだけを返す。
    札の判定は scope に依らず、変更前に無い本文かどうかだけで決まる。
    """
    if base_root is None:
        # 変更前の木が無い種類（設計書と機能の全文のレビュー）は、どの正規表現が変更かを言えない。
        # 比べずに全件を札にすると変えていない正規表現まで違反になるので、札を作らない
        return RegexTagFindings((), ())
    found: list[RegexDetection] = []
    violations: list[AnnotationViolation] = []
    for path in sorted(set(paths)):
        source = read_regex_source(root, path)
        if source is None:
            continue
        scan = scan_allow_comments(source.adapter, source.root, "regex", {str(r) for r in Rule})
        before = {lit.pattern for lit in _literals(base_root, renames.get(path, path))}
        for literal in source.scan(path).literals:
            # 変更前にも在る本文は、このレビューで変えた正規表現ではない
            if literal.pattern in before:
                continue
            verdict = judge(literal)
            if isinstance(verdict, CountedRegex):
                for tag in verdict.tags:
                    reason = scan.allowed.get(AllowedSpot(str(tag.rule), literal.line))
                    found.append(
                        RegexDetection(
                            literal, tag, Active() if reason is None else Allowed(reason)
                        )
                    )
        rows = split_rows(source.data.decode("utf-8", errors="replace"))
        violations.extend(
            AnnotationViolation(path, line, rows[line - 1].strip()[:EXCERPT_LIMIT])
            for line in scan.malformed
            if scope.covers(path, line)
        )
    return RegexTagFindings(tuple(found), tuple(violations))


def regex_entry(number: str, detection: RegexDetection) -> GateEntry:
    tag = detection.tag
    severity = (
        GateSeverity.REGEX_NIT if tag.severity is Severity.NIT else GateSeverity.REGEX_WARNING
    )
    return GateEntry(
        number,
        str(tag.rule),
        detection.literal.path,
        detection.literal.line,
        detection.literal.pattern,
        explain(tag).to_json(),
        severity,
        RegexDetail(),
        detection.state,
    )


def annotation_entry(number: str, violation: AnnotationViolation) -> GateEntry:
    return GateEntry(
        number,
        REGEX_ANNOTATION_ID,
        violation.path,
        violation.line,
        violation.excerpt,
        ANNOTATION_EXPLANATION.to_json(),
        GateSeverity.REGEX_WARNING,
        RegexDetail(),
    )
