"""テストのファイルから flaky の型を探す。"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from tree_sitter import Language, Node, Query, QueryCursor

from jigane.flaky.catalog import ANNOTATION_ID, Item, QuerySpec
from jigane.flaky.checks import CHECKS, CONTEXT_CHECKS, Finding
from jigane.flaky.setup import FileContext, conftest_context, fixations_in
from jigane.gates.line_scope import LineScope
from jigane.git.tree_files import read_tree_bytes
from jigane.judgment_map.allow_annotation import AllowedSpot, scan_allow_comments, split_rows
from jigane.languages import registry
from jigane.languages.base import LanguageAdapter, grammar_target, parse
from jigane.languages.grammars import get_language

EXCERPT_LIMIT = 200


@dataclass(frozen=True)
class Detection:
    item_id: str
    path: str
    line: int
    # 検出した行の本文。前後の空白は除く
    text: str
    # 静的に確かめきれない当たり。兆候の型に下げて警告にする
    undetermined: bool = False
    # 検出の理由を一文で。理由を持たない検出では空
    reason: str = ""


@dataclass(frozen=True)
class QueryDetection(Detection):
    """query に当たった検出。note、direction、equivalent は spec から引く。"""

    spec: QuerySpec = field(kw_only=True)


@dataclass(frozen=True)
class AnnotationDetection(Detection):
    """注記の書式の違反。query が無いので spec も、相当する既製の規則も持たない。"""


@cache
def _compile(language: Language, source: str) -> Query:
    return Query(language, source)


def _compiled(adapter: LanguageAdapter, source: str) -> Query:
    return _compile(get_language(grammar_target(adapter)), source)


def _hits(
    adapter: LanguageAdapter,
    root: Node,
    source: bytes,
    spec: QuerySpec,
    context: FileContext,
) -> list[tuple[Node, Finding | None]]:
    query = _compiled(adapter, spec.query)
    hits: list[tuple[Node, Finding | None]] = []
    for _, captures in QueryCursor(query).matches(root):
        for node in captures.get("hit", []):
            if spec.check is None:
                result: bool | Finding = True
            elif spec.check in CONTEXT_CHECKS:
                result = CONTEXT_CHECKS[spec.check](node, source, context)
            else:
                result = CHECKS[spec.check](node, source)
            if result is False:
                continue
            hits.append((node, result if isinstance(result, Finding) else None))
    return hits


def detect_source(
    path: str,
    source: bytes,
    adapter: LanguageAdapter,
    catalog: list[Item],
    scope: LineScope,
    *,
    context: FileContext | None = None,
) -> list[Detection]:
    """scope が covers する行に当たる検出だけを返す。

    context はファイルの外の共通のセットアップ。省略時は、ファイル自身の固定だけを見る。
    """
    root = parse(adapter, source).root_node
    own = fixations_in(path, source, adapter) if adapter.name == "python" else FileContext()
    context = own if context is None else context.merged(own)
    text_lines = split_rows(source.decode("utf-8", errors="replace"))

    def excerpt(line: int) -> str:
        return text_lines[line - 1].strip()[:EXCERPT_LIMIT]

    scan = scan_allow_comments(adapter, root, "flaky", {item.id for item in catalog})
    allowed = scan.allowed
    found: list[Detection] = [
        AnnotationDetection(ANNOTATION_ID, path, line, excerpt(line)) for line in scan.malformed
    ]
    seen: set[tuple[str, int]] = set()
    for item in catalog:
        for spec in item.queries:
            if spec.language != adapter.name:
                continue
            for node, finding in _hits(adapter, root, source, spec, context):
                line = node.start_point.row + 1
                if AllowedSpot(item.id, line) in allowed or (item.id, line) in seen:
                    continue
                seen.add((item.id, line))
                found.append(
                    QueryDetection(
                        item.id,
                        path,
                        line,
                        excerpt(line),
                        finding.undetermined if finding else False,
                        finding.reason if finding else "",
                        spec=spec,
                    )
                )
    return [d for d in found if scope.covers(path, d.line)]


def detect(
    root: Path,
    paths: Sequence[str],
    catalog: list[Item],
    scope: LineScope,
) -> list[Detection]:
    """scope が全行ならファイル全体（機能レビュー式）、変えた行なら差分レビュー式。"""
    found: list[Detection] = []
    contexts: dict[str, FileContext] = {}
    for path in paths:
        adapter = registry.adapter_for(path)
        if adapter is None or not registry.is_test_path(path, [adapter]):
            continue
        source = read_tree_bytes(root, path)
        if source is None:
            continue
        # conftest の階層はディレクトリごとに一度だけ読む
        directory = str(Path(path).parent)
        if directory not in contexts:
            contexts[directory] = (
                conftest_context(root, path, adapter)
                if adapter.name == "python"
                else FileContext()
            )
        found.extend(
            detect_source(path, source, adapter, catalog, scope, context=contexts[directory])
        )
    return sorted(found, key=lambda d: (d.path, d.line, d.item_id))
