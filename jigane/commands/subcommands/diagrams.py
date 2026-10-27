"""`jigane diagrams`: 概要の画面の図の節に当たる、ツリー図とシーケンス図を出す。"""

import argparse
import json

from jigane.commands.context import Context
from jigane.commands.exit_codes import EXIT_OK
from jigane.figures.models import (
    DiffTreeFigure,
    MermaidFigure,
    MovedSignature,
    SequenceFigure,
    SignatureFigure,
    TableFigure,
    TreeFigure,
)
from jigane.reviews.lifecycle import load_review, resolve_review
from jigane.skills.policy_figures import PlacedFigure, diagrams_of


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    diagrams = subparsers.add_parser(
        "diagrams", help="ツリー図、シーケンス図、方針のレビューの図を出します"
    )
    diagrams.add_argument("review")
    diagrams.add_argument("--json", action="store_true")
    diagrams.set_defaults(handler=run)


def _fenced(source: object) -> str:
    return f"```mermaid\n{str(source).rstrip()}\n```"


def _mermaid_blocks(title: str, figure: MermaidFigure) -> list[str]:
    blocks = [title, figure.description]
    for panel in figure.panels:
        blocks.extend(filter(None, [panel.title, _fenced(panel.mermaid)]))
    if figure.note is not None:
        blocks.append(figure.note)
    return blocks


def _signature_blocks(title: str, figure: SignatureFigure) -> list[str]:
    blocks = [title]
    for entry in figure.entries:
        if isinstance(entry, MovedSignature):
            blocks.extend(
                [f"{entry.name}（移した（{entry.old_place} → {entry.new_place}））", entry.text]
            )
            continue
        blocks.extend(
            [
                f"{entry.name}（{entry.place}）",
                f"- 変更前: {entry.old_text()}",
                f"+ 変更後: {entry.new_text()}",
            ]
        )
    return blocks


def _tree_blocks(figure: TreeFigure) -> list[str]:
    panels = [figure.after]
    if isinstance(figure, DiffTreeFigure):
        panels.insert(0, figure.before)
    return [block for panel in panels for block in (panel.title, _fenced(panel.mermaid))]


def _sequence_blocks(figure: SequenceFigure) -> list[str]:
    blocks: list[str] = []
    for flow in figure.flows:
        blocks.append(f"シーケンス図: {flow.title}")
        for panel in flow.panels:
            blocks.extend([panel.title, _fenced(panel.mermaid)])
        if flow.note is not None:
            blocks.append(flow.note.display)
    blocks.extend(note.display for note in figure.notes)
    return blocks


def _figure_blocks(placed: PlacedFigure) -> list[str]:
    figure = placed.figure
    if isinstance(figure, TreeFigure):
        return _tree_blocks(figure)
    if isinstance(figure, SequenceFigure):
        return _sequence_blocks(figure)
    if isinstance(figure, SignatureFigure):
        return _signature_blocks(placed.title, figure)
    if isinstance(figure, TableFigure):
        return [placed.title, figure.text()]
    return _mermaid_blocks(placed.title, figure)


def run(args: argparse.Namespace, context: Context) -> int:
    del context  # 図の組み立ては LLM を呼ばない
    result = diagrams_of(load_review(resolve_review(args.review)))
    if args.json:
        print(json.dumps(result.to_json(), ensure_ascii=False, indent=2))
        return EXIT_OK
    blocks = [block for placed in result.figures for block in _figure_blocks(placed)]
    for entry in result.unsupported:
        blocks.extend([entry.title, entry.note()])
    print("\n".join(blocks))
    return EXIT_OK
