"""方針のレビューの一層目。設計の原則ごとに、その原則に関わる図だけを渡す一起動のプロンプトを組む。"""

from dataclasses import dataclass

from jigane.groups.viewpoints import (
    KISS,
    SEPARATION_OF_CONCERNS,
    SINGLE_SOURCE,
    YAGNI,
    Viewpoint,
    design_principles,
)
from jigane.reviews.catalog import scope_files
from jigane.skills import adversarial, macro, policy_figures
from jigane.skills.assembly import AssemblyInput
from jigane.skills.macro import PolicySections
from jigane.skills.policy_figures import REGISTRY, FigureKey
from jigane.skills.policy_principle_shapes import Shape, present_shapes

PROMPT = "macro_principles"

TREE_KEY: FigureKey = "tree"

FIGURE_KEYS_BY_PRINCIPLE: dict[Viewpoint, tuple[FigureKey, ...]] = {
    SINGLE_SOURCE: ("value_sets", "cochange", "classes", "field_copies"),
    YAGNI: ("sequence", "classes", "readers", "callers"),
    KISS: ("value_sets", "classes", "discriminators"),
    SEPARATION_OF_CONCERNS: ("sequence", "classes", "readers"),
}

# 原則を起動する図の形。どれか一つがあれば起動し、一つも無ければ見送る
SHAPES_BY_PRINCIPLE: dict[Viewpoint, frozenset[Shape]] = {
    SINGLE_SOURCE: frozenset({Shape.VALUE_SETS, Shape.FIELD_COPIES}),
    YAGNI: frozenset({Shape.CALLERS, Shape.SINGLE_IMPLEMENTATION}),
    KISS: frozenset(
        {Shape.VALUE_SETS, Shape.DISCRIMINATORS, Shape.INHERITANCE, Shape.WIDE_SIGNATURE}
    ),
    SEPARATION_OF_CONCERNS: frozenset({Shape.SEQUENCE}),
}


@dataclass(frozen=True)
class PrinciplePrompt:
    """一つの原則の一起動。principle.number が、その起動の指摘の viewpoint になる。"""

    principle: Viewpoint
    figure_keys: tuple[FigureKey, ...]
    prompt: str


def figure_keys_of(principle: Viewpoint) -> tuple[FigureKey, ...]:
    """その原則に渡す図の鍵。ツリー図を含め、登録簿の順で返す。"""
    keys = {*FIGURE_KEYS_BY_PRINCIPLE[principle], TREE_KEY}
    return tuple(figure.key for figure in REGISTRY if figure.key in keys)


def principle_text(principle: Viewpoint) -> str:
    """雛形の `$principles` に差し込む、一つの原則の行。"""
    return f"- {principle.number} {principle.name}: {principle.question}"


def principle_prompt(
    assembly: AssemblyInput, principle: Viewpoint, sections: PolicySections | None = None
) -> str:
    """一つの原則を問うプロンプト。資料はその原則に割り当てた図の節だけ。

    sections は事前に組んだ節で、四本を続けて組むときは一度だけ作って渡す。
    """
    review = assembly.review
    sections = macro.policy_sections(review) if sections is None else sections
    # 技術選定の節と前の回の指摘の節は、図の起動と同じく資料の末尾に置く
    materials = "\n\n".join(
        [
            *policy_figures.prompt_figure_texts(
                assembly, sections.figures, figure_keys_of(principle)
            ),
            sections.technology,
            f"### {macro.SECTION_HISTORY}\n\n{macro.history_text(assembly)}",
        ]
    )
    return adversarial.load_prompt(PROMPT).substitute(
        mode=review.mode,
        target_files="\n".join(f"- {path}" for path in review.paths),
        declaration=macro.declaration_prompt_text(review),
        background=macro.background_prompt_text(review),
        materials=materials,
        scope="\n".join(f"- {path}" for path in sorted(scope_files(review))),
        principles=principle_text(principle),
    )


def principle_prompts(assembly: AssemblyInput) -> tuple[PrinciplePrompt, ...]:
    """問える図の形がある原則だけの、原則ごとのプロンプト。表の順（P1〜P4）で返す。"""
    sections = macro.policy_sections(assembly.review)
    shapes = present_shapes(assembly, sections.sequence)
    return tuple(
        PrinciplePrompt(
            principle=principle,
            figure_keys=figure_keys_of(principle),
            prompt=principle_prompt(assembly, principle, sections),
        )
        for principle in design_principles()
        if SHAPES_BY_PRINCIPLE[principle] & shapes
    )
