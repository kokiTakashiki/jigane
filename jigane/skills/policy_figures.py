"""方針の図の登録簿。

方針のレビューの図だけの形の資料と、`jigane diagrams` の図は、どちらもこの
登録簿を回して作る。図が一つ増えるときに触るのは、`REGISTRY` の一行と、その図を作る関数だけ。
図の構造と、文と JSON への変換は `jigane.figures.models` が持つ。
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any

from jigane.figures.models import (
    TREE_TITLE,
    Figure,
    SequenceFigure,
    TreeFigure,
)
from jigane.languages import registry
from jigane.languages.base import (
    CommandArgumentAdapter,
    ConcurrencyAdapter,
    FilePathAdapter,
    RegexAdapter,
    SequenceAdapter,
    StoreAccessReader,
)
from jigane.policy_materials import changed_files, classes, discriminators, er
from jigane.policy_materials.changed_files import PrebuiltSection
from jigane.reviews.lifecycle import Review
from jigane.sequence import review_diagrams
from jigane.skills import (
    policy_commands,
    policy_concurrency,
    policy_file_paths,
    policy_regex,
    policy_stores,
)


class FigureSlot(StrEnum):
    """登録簿の図が後に続く置き場。事前に組む節の置き場は `PrebuiltSection`。"""

    AFTER_TREE = "after_tree"
    AFTER_REGIONS = "after_regions"


PromptPlace = PrebuiltSection | FigureSlot


class LanguageAgnostic(Enum):
    """図が言語アダプタの口に依らないことを表す値。None に意味を持たせないための明示の値。"""

    VALUE = "language_agnostic"


LANGUAGE_AGNOSTIC = LanguageAgnostic.VALUE

# プロンプトの図の節の並び。事前に組む節と、登録簿の図の置き場を交互に並べる。
# 置き場の中の並びは登録簿の順
PROMPT_ORDER: tuple[PromptPlace, ...] = (
    PrebuiltSection.TREE,
    FigureSlot.AFTER_TREE,
    PrebuiltSection.SEQUENCE,
    PrebuiltSection.REGIONS,
    FigureSlot.AFTER_REGIONS,
)


FigureKey = str
"""登録簿の図の識別子。--json の key と同じ語。"""

# 方針の資料から外す図の鍵。外す図の代わりに載せる情報の置き場は、それぞれ図の側に持つ
# （signatures の変更前の形はクラス図の `~` の項目）。画面と CLI の図は外さない
PROMPT_OMITTED: frozenset[FigureKey] = frozenset({"signatures"})


def _tree_figure(review: Review) -> TreeFigure:
    return changed_files.tree_figure(review)


def _sequence_figure(review: Review) -> SequenceFigure:
    return review_diagrams.sequence_figure(review)


@dataclass(frozen=True)
class PolicyFigure:
    """登録簿の一項。

    build は図の構造を返す。描くものが無いときは None を返し、資料では節ごと省かれる。
    slot は資料の中の置き場。ツリー図は `PrebuiltSection.TREE`、シーケンス図は
    `PrebuiltSection.SEQUENCE` で、この二つの節はプロンプトでは事前に組んだ本文から引く。
    build を呼ぶのは画面と CLI だけ。それ以外はツリー図の直後（`FigureSlot.AFTER_TREE`）か、
    判断の箇所の直後（`FigureSlot.AFTER_REGIONS`）。
    """

    key: FigureKey
    title: str
    build: Callable[[Review], Figure | None]
    slot: PromptPlace
    # 図が言語アダプタの口に依るときの、その口の Protocol。口を持たない言語は「対応外」と示す。
    # クラス図、ER、判別子、読み手は、型と項目の宣言を SequenceAdapter の索引（build_index）から
    # 引くので、口は SequenceAdapter になる。ツリーとシグネチャは言語の口に依らないので
    # LANGUAGE_AGNOSTIC。既定値は持たず、図ごとに必ず書く
    interface: type | LanguageAgnostic


REGISTRY: tuple[PolicyFigure, ...] = (
    PolicyFigure(
        "tree",
        TREE_TITLE,
        _tree_figure,
        PrebuiltSection.TREE,
        interface=LANGUAGE_AGNOSTIC,
    ),
    PolicyFigure(
        "sequence",
        "シーケンス図",
        _sequence_figure,
        PrebuiltSection.SEQUENCE,
        interface=SequenceAdapter,
    ),
    PolicyFigure(
        "concurrency",
        policy_concurrency.SECTION_CONCURRENCY,
        policy_concurrency.concurrency_diagram,
        FigureSlot.AFTER_TREE,
        interface=ConcurrencyAdapter,
    ),
    PolicyFigure(
        "classes",
        classes.SECTION_CLASSES,
        classes.class_diagram,
        FigureSlot.AFTER_TREE,
        interface=SequenceAdapter,
    ),
    PolicyFigure(
        "er",
        er.SECTION_ER,
        er.er_diagram,
        FigureSlot.AFTER_TREE,
        interface=SequenceAdapter,
    ),
    PolicyFigure(
        "discriminators",
        discriminators.SECTION_DISCRIMINATORS,
        discriminators.discriminator_diagram,
        FigureSlot.AFTER_TREE,
        interface=SequenceAdapter,
    ),
    PolicyFigure(
        "readers",
        discriminators.SECTION_READERS,
        discriminators.reader_diagram,
        FigureSlot.AFTER_TREE,
        interface=SequenceAdapter,
    ),
    PolicyFigure(
        "commands",
        policy_commands.SECTION_COMMANDS,
        policy_commands.command_table,
        FigureSlot.AFTER_TREE,
        interface=CommandArgumentAdapter,
    ),
    PolicyFigure(
        "file_paths",
        policy_file_paths.SECTION_FILE_PATHS,
        policy_file_paths.file_path_table,
        FigureSlot.AFTER_TREE,
        interface=FilePathAdapter,
    ),
    PolicyFigure(
        "stores",
        policy_stores.SECTION_STORES,
        policy_stores.store_table,
        FigureSlot.AFTER_TREE,
        interface=StoreAccessReader,
    ),
    PolicyFigure(
        "signatures",
        changed_files.SECTION_SIGNATURES,
        changed_files.signature_changes,
        FigureSlot.AFTER_REGIONS,
        interface=LANGUAGE_AGNOSTIC,
    ),
    PolicyFigure(
        "regex",
        policy_regex.SECTION_REGEX,
        policy_regex.regex_table,
        FigureSlot.AFTER_REGIONS,
        interface=RegexAdapter,
    ),
)


@dataclass(frozen=True)
class UnsupportedLanguage:
    """図の口を持たない言語の名前と出どころ。出どころは登録簿の一か所から引く。"""

    name: str
    builtin: bool

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "builtin": self.builtin}


def unsupported_languages(review: Review, figure: PolicyFigure) -> list[UnsupportedLanguage]:
    """変更したファイルの言語のうち、図の口を持たない言語。対応外の判定はここだけが持つ。

    口を持つかは、アダプタが図の Protocol を実装しているかで決める。言語のアダプタが無い
    ファイル（文書など）は、どの言語の対応外でもないので数えない。名前の昇順に並べる。
    """
    if figure.interface is LANGUAGE_AGNOSTIC:
        return []
    found: dict[str, UnsupportedLanguage] = {}
    for path in changed_files.file_changes(review):
        adapter = registry.adapter_for(path)
        if adapter is not None and not isinstance(adapter, figure.interface):
            name = registry.label_for(adapter.name)
            found[name] = UnsupportedLanguage(name, registry.is_builtin(adapter.name))
    return [found[name] for name in sorted(found)]


def unsupported_note(languages: Sequence[UnsupportedLanguage]) -> str:
    """対応外の言語を示す文。資料と CLI と画面で同じ文を使う。

    組み込みの言語の文を先に、外から入れた言語の文を後に、空白一つで区切って並べる。
    """
    sentences = []
    for builtin, reason in (
        (True, "jigane の {names} の解析は、この表の読み取りにまだ対応していない"),
        (False, "入れたプラグインが、この表の読み取りを持たない"),
    ):
        names = "、".join(language.name for language in languages if language.builtin == builtin)
        if names:
            sentences.append(f"対応外の言語: {names}（{reason.format(names=names)}）。")
    return " ".join(sentences)


def section_texts(review: Review, slot: FigureSlot) -> list[str]:
    """資料に載せる、その置き場の図の節。描くものが無い図の節は載せない。"""
    sections = []
    for figure in REGISTRY:
        if figure.slot != slot or figure.key in PROMPT_OMITTED:
            continue
        built = figure.build(review)
        languages = unsupported_languages(review, figure)
        # 表が空でも、対応外の言語があれば節を残す。表が出ない理由が口の無さだと読み手に伝えるため
        parts = [built.text()] if built is not None else []
        if languages:
            parts.append(unsupported_note(languages))
        if parts:
            sections.append(f"### {figure.title}\n\n" + "\n\n".join(parts))
    return sections


def _with_unsupported_note(review: Review, place: PrebuiltSection, section: str) -> str:
    """事前に組んだ節の末尾に、その節の図の対応外の言語の一文を足す。

    節の本文は図が描けなかったときの文でもよい。描けない理由が口の無さだと読み手に伝えるため、
    対応外の言語があれば本文の有無に依らず一文を付ける。口に依らない図（ツリー）は何も足さない。
    """
    for figure in REGISTRY:
        languages = unsupported_languages(review, figure) if figure.slot == place else []
        if languages:
            return f"{section}\n\n{unsupported_note(languages)}"
    return section


def prompt_figure_texts(review: Review, prebuilt: Mapping[PrebuiltSection, str]) -> list[str]:
    """プロンプトの資料に載せる図の節を、`PROMPT_ORDER` と登録簿の順で並べる。

    prebuilt は事前に組んだ節を `PrebuiltSection` で引く辞書。ツリー図、シーケンス図、
    判断の箇所の一覧は重いので組み直さず、ここから引く。登録簿の順を替えると、同じ置き場の節の順が替わる。
    """
    texts = []
    for place in PROMPT_ORDER:
        if isinstance(place, PrebuiltSection):
            texts.append(_with_unsupported_note(review, place, prebuilt[place]))
        else:
            texts.extend(section_texts(review, place))
    return texts


@dataclass(frozen=True)
class PlacedFigure:
    """画面と CLI に渡す、登録簿の鍵と見出しを持つ図一つ。"""

    key: str
    title: str
    figure: Figure

    def to_json(self) -> dict[str, Any]:
        return {"key": self.key, "title": self.title, **self.figure.to_json()}


def placed_figures(review: Review) -> list[PlacedFigure]:
    """登録簿を回して、描くものがある図を登録簿の順に並べる。"""
    placed = []
    for entry in REGISTRY:
        built = entry.build(review)
        if built is not None and built.is_drawable():
            placed.append(PlacedFigure(entry.key, entry.title, built))
    return placed


@dataclass(frozen=True)
class UnsupportedFigure:
    """図の口を持たない言語を含む変更の、その図の鍵と見出しと言語名。"""

    key: str
    title: str
    languages: tuple[UnsupportedLanguage, ...]

    def note(self) -> str:
        return unsupported_note(self.languages)

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "languages": [language.to_json() for language in self.languages],
            "note": self.note(),
        }


def unsupported_figures(review: Review) -> list[UnsupportedFigure]:
    """登録簿の順に、対応外の言語がある図を並べる。図が描けたかには依らない。"""
    result = []
    for entry in REGISTRY:
        languages = unsupported_languages(review, entry)
        if languages:
            result.append(UnsupportedFigure(entry.key, entry.title, tuple(languages)))
    return result


@dataclass(frozen=True)
class DiagramsResult:
    """`diagrams_of` の戻り値。figures は登録簿の順で、描くものがある図だけを持つ。

    unsupported は、変更した言語の一部が口を持たない図。
    """

    figures: tuple[PlacedFigure, ...]
    unsupported: tuple[UnsupportedFigure, ...] = ()

    def to_json(self) -> dict[str, Any]:
        """`--json` へ出す辞書。図の dataclass を辞書にするのはここだけ。"""
        return {
            "figures": [placed.to_json() for placed in self.figures],
            "unsupported": [entry.to_json() for entry in self.unsupported],
        }


def diagrams_of(review: Review) -> DiagramsResult:
    """概要の画面と CLI の図。ツリー図もシーケンス図も、登録簿を回して作る。"""
    return DiagramsResult(tuple(placed_figures(review)), tuple(unsupported_figures(review)))
