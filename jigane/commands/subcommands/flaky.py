"""`jigane flaky`: レビューを作らずに、テストの flaky の検査だけを走らせる。"""

import argparse
import json
from pathlib import Path

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_GATE_VIOLATION, EXIT_OK
from jigane.flaky.catalog import load_catalog
from jigane.flaky.detector import detect
from jigane.flaky.finding import fails, of_kind, to_finding
from jigane.gates.entry import GateKind
from jigane.gates.line_scope import AllLines
from jigane.git.tree_files import work_files
from jigane.reviews.lifecycle import ReviewError


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    flaky = subparsers.add_parser(
        "flaky", help="テストの flaky の検査だけを走らせます（レビューは作りません）"
    )
    flaky.add_argument("paths", nargs="*", metavar="パス", help="ファイルかディレクトリです")
    flaky.add_argument("--strict", action="store_true", help="警告でも終了コード 1 にします")
    flaky.add_argument("--json", action="store_true")
    flaky.set_defaults(handler=run)


def _relative(root: Path, argument: str) -> str:
    resolved = (root / argument).resolve()
    if not resolved.exists():
        raise ReviewError(f"パスがありません: {argument}")
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        raise ReviewError(f"作業ディレクトリの外のパス: {argument}") from None


def _scope(root: Path, arguments: list[str]) -> list[str]:
    files = work_files(root, include_untracked=True).files
    if not arguments:
        return list(files)
    targets = [_relative(root, argument) for argument in arguments]
    return [
        path
        for path in files
        if any(target in {".", path} or path.startswith(f"{target}/") for target in targets)
    ]


def run(args: argparse.Namespace, context: Context) -> int:
    del context  # 検査は静的で、LLM も JIGANE_HOME も使わない
    root = Path.cwd()
    catalog = load_catalog()
    by_id = {item.id: item for item in catalog}
    detections = detect(root, _scope(root, args.paths), catalog, AllLines())
    findings = [to_finding(detection, by_id) for detection in detections]
    violations = of_kind(findings, GateKind.VIOLATION)
    warnings = of_kind(findings, GateKind.WARNING)
    if args.json:
        result = {
            "violations": [f.to_json() for f in violations],
            "warnings": [f.to_json() for f in warnings],
            "counts": {"violations": len(violations), "warnings": len(warnings)},
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for finding in findings:
            print("\n".join(finding.to_lines()))
        print(f"違反 {len(violations)} 件、警告 {len(warnings)} 件")
    return EXIT_GATE_VIOLATION if fails(findings, strict=args.strict) else EXIT_OK
