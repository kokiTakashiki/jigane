"""方針の図の構造。図を作る関数はこの型を返し、プロンプトの文と JSON の辞書はこの型から作る。

図の項目の並びはここの frozen dataclass が唯一の形で、画面向けの別の型は持たない。
CLI の文字表示はこの dataclass を直接読む。JSON へ変えるのは出力の境目だけで、
変換は型ごとの `to_json` に置く。JSON の `kind` の欄には `FigureKind` が入る。
このモジュールは他の図のモジュールから読まれるので、図のモジュールを読み込まない。
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, ClassVar

from jigane.markdown.cells import markdown_table

DELETED_SIGNATURE_TEXT = "（削除）"
ADDED_SIGNATURE_TEXT = "（新規）"
NO_SIGNATURE_CHANGE_TEXT = "公開の宣言のシグネチャの変更は無い。"
NO_FILES_TEXT = "変更したファイルは無い。"
NO_BEFORE_TREE_TEXT = "変更前の木に置くファイルは無い。"
NO_AFTER_TREE_TEXT = "変更後の木に置くファイルは無い。"
_TREE_READING = (
    "線は親のディレクトリから子への包含を表し、ファイルどうしの依存や参照は描かない。"
    "ファイルが無く子が一つだけのディレクトリは、子と一つの節点に縮めて `a/b/` のように書く。"
)
TREE_INTRO = (
    "ツリー図は、変更したファイルをディレクトリとファイルのツリーで示す。"
    + _TREE_READING
    + "ファイルの札の印は、追加、削除、変更、移動のいずれかで、"
    "移動したファイルは移動先に置き、移動元を札に添える。"
)
PATH_TREE_INTRO = (
    "ツリー図は、選んだファイルをディレクトリとファイルのツリーで示す。"
    + _TREE_READING
    + "変更の印は付けない。"
)
TREE_DIFF_INTRO = (
    "ツリー図は、変更したファイルをディレクトリとファイルのツリー二枚で示す。"
    "変更前の木には変更前の木にあったファイルだけを、変更後の木には変更後の木にあるファイルだけを置く。"
    + _TREE_READING
    + "ファイルの札の印は、追加、削除、変更、移動のいずれかで、"
    "追加は変更後の木にだけ、削除は変更前の木にだけ、変更は両方の木に置く。"
    "移動したファイルは変更前の木では移動元に置いて札に移動先を、変更後の木では移動先に置いて札に移動元を添える。"
)
SEQUENCE_INTRO = (
    "入口ごとの呼び出しの流れを Mermaid で示す。"
    "札のある図は変更後だけを示し、変更前で消えた呼び出しは「（削除）」、変更後で増えた呼び出しは「（追加）」の札で図の中に残る。"
    "札の無い図には変更前の図が残る。"
    "判断の箇所の番号を持つ図を先に、残りは呼び出しの多い順に、すべて渡す。"
)


class FigureKind(StrEnum):
    TREE = "tree"
    SEQUENCE = "sequence"
    MERMAID = "mermaid"
    SIGNATURES = "signatures"
    TABLE = "table"


# 画面の見出しと図の登録簿の名札が、この名前でツリー図を呼ぶ
TREE_TITLE = "ツリー図"


class FileState(StrEnum):
    """変更したファイル一つの状態。ツリー図の節点の札と資料の一覧に使う。"""

    ADDED = "added"
    DELETED = "deleted"
    CHANGED = "changed"
    MOVED = "moved"
    # 差分を持たない現状の木の葉。変更の印も色も付けない
    UNCHANGED = "unchanged"

    @property
    def label(self) -> str:
        """節点の札と資料の一覧に書く語。"""
        return _FILE_STATE_LABELS[self]

    @property
    def css_class(self) -> str:
        """Mermaid の節点に付ける class の名前。"""
        return self.value


_FILE_STATE_LABELS = {
    FileState.ADDED: "追加",
    FileState.DELETED: "削除",
    FileState.CHANGED: "変更",
    FileState.MOVED: "移動",
    FileState.UNCHANGED: "変更なし",
}


class SignatureChange(StrEnum):
    ADDED = "added"
    DELETED = "deleted"
    CHANGED = "changed"
    MOVED = "moved"


@dataclass(frozen=True)
class Panel:
    """Mermaid の図一つ。title は図の見出しで、無ければ空文字列。"""

    title: str
    mermaid: str

    def to_json(self) -> dict[str, Any]:
        return {"title": self.title, "mermaid": self.mermaid}


@dataclass(frozen=True)
class NoteText:
    """シーケンス図の注記一つの二本の文。material は資料に入る常体、display は利用者に見せる敬体。

    二本はいつも同じ内容を指すので、片方だけを持つ値は作れない（既定値を置かない）。
    """

    material: str
    display: str


@dataclass(frozen=True)
class SequenceFlow:
    """入口一つの呼び出しの流れ。title は入口の修飾名と位置、note は変更前の図が無い理由。

    regions は図に札として載る判断の箇所の番号（J3 など）を、現れた順に重複なく並べたもの。
    資料で判断の箇所を持つ入口を先に並べるときは、Mermaid の原文でなくこの項目を引く。
    """

    title: str
    panels: tuple[Panel, ...]
    note: NoteText | None
    regions: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "panels": [panel.to_json() for panel in self.panels],
            "note": None if self.note is None else self.note.display,
        }


@dataclass(frozen=True)
class FileSummary:
    """変更したファイル一つ。"""

    path: str
    state: FileState
    lines: int


@dataclass(frozen=True)
class TreeFigure:
    """ツリー図のうち、機能レビュー式の形。変更後の木が一枚だけで、変更前は無い。

    差分レビュー式は変更前の木を足した `DiffTreeFigure`。変更前が無いことは、
    型で表し、欄を空にして表さない。files は変更したファイルで、資料の文にだけ使う。
    files が空のときは描くものが無く、木の原文は空文字列になる。
    files の状態がすべて `FileState.UNCHANGED` のときは差分を持たない現状の木で、
    変更の印と合計を資料の文に出さない。
    """

    after: Panel
    files: tuple[FileSummary, ...]

    kind: ClassVar[FigureKind] = FigureKind.TREE
    intro: ClassVar[str] = TREE_INTRO

    def _tree_blocks(self) -> list[str]:
        """資料の文に並べる、木ごとの見出しつきの囲み。機能レビュー式は見出しを付けない。"""
        return [self._fenced(self.after, NO_AFTER_TREE_TEXT)]

    @staticmethod
    def _fenced(panel: Panel, empty_text: str) -> str:
        return f"```mermaid\n{panel.mermaid}\n```" if panel.mermaid else empty_text

    def text(self) -> str:
        """資料の文。囲みつきのツリー図とファイルの一覧。"""
        if not self.files:
            return NO_FILES_TEXT
        diagram = "\n\n".join(self._tree_blocks())
        if self._is_current():
            summary = "\n".join(f"- {f.path}" for f in sorted(self.files, key=lambda f: f.path))
            return f"{PATH_TREE_INTRO}\n\n{diagram}\n\n{summary}"
        total_lines = sum(f.lines for f in self.files)
        ordered = sorted(self.files, key=lambda f: f.path)
        summary = "\n".join(f"- {f.path}（{f.state.label}、{f.lines} 行）" for f in ordered)
        total = f"変更の合計は {len(self.files)} ファイル、{total_lines} 行。"
        return f"{self.intro}\n\n{diagram}\n\n{total}\n\n{summary}"

    def _is_current(self) -> bool:
        return all(f.state is FileState.UNCHANGED for f in self.files)

    def is_drawable(self) -> bool:
        return bool(self.files)

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "title": TREE_TITLE, "after": self.after.to_json()}


@dataclass(frozen=True)
class DiffTreeFigure(TreeFigure):
    """差分レビュー式のツリー図。変更前の木と変更後の木の二枚。"""

    before: Panel

    intro: ClassVar[str] = TREE_DIFF_INTRO

    def _tree_blocks(self) -> list[str]:
        return [
            f"#### 変更前\n\n{self._fenced(self.before, NO_BEFORE_TREE_TEXT)}",
            f"#### 変更後\n\n{self._fenced(self.after, NO_AFTER_TREE_TEXT)}",
        ]

    def to_json(self) -> dict[str, Any]:
        return {**super().to_json(), "before": self.before.to_json()}


@dataclass(frozen=True)
class SequenceFigure:
    """入口ごとのシーケンス図。notes は図にしなかった入口や、図を出せなかった理由の文。"""

    flows: tuple[SequenceFlow, ...]
    notes: tuple[NoteText, ...] = ()

    kind: ClassVar[FigureKind] = FigureKind.SEQUENCE

    def without_tagged_before(self) -> "SequenceFigure":
        """判断の箇所の札を持つ入口から、変更前の図を外した図。変更後の図だけが残る。

        札を持たない入口は変更前の図も残す。外した入口に変更前の図が無い理由の注記は元から無い。
        """
        flows = tuple(
            replace(flow, panels=flow.panels[-1:]) if flow.regions else flow for flow in self.flows
        )
        return replace(self, flows=flows)

    def text(self) -> str:
        """資料の文。判断の箇所の番号を持つ入口を先に、残りを元の順で、すべて載せる。"""
        if not self.flows:
            return " ".join(note.material for note in self.notes)
        tagged = [f for f in self.flows if f.regions]
        untagged = [f for f in self.flows if not f.regions]
        chosen = [*tagged, *untagged]
        blocks = [SEQUENCE_INTRO]
        for flow in chosen:
            lines = [f"#### {flow.title}", ""]
            for panel in flow.panels:
                lines += [
                    f"{panel.title}",
                    "",
                    "```mermaid",
                    panel.mermaid.rstrip("\n"),
                    "```",
                    "",
                ]
            if flow.note is not None:
                lines += [f"変更前の図は無い: {flow.note.material}", ""]
            blocks.append("\n".join(lines).rstrip("\n"))
        return "\n\n".join(blocks)

    def is_drawable(self) -> bool:
        return bool(self.flows or self.notes)

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "flows": [flow.to_json() for flow in self.flows],
            "notes": [note.display for note in self.notes],
        }


@dataclass(frozen=True)
class MermaidFigure:
    """説明、Mermaid の図の列、注記でできた図。panels が空のときは描くものが無い。"""

    description: str
    panels: tuple[Panel, ...]
    note: str | None = None

    kind: ClassVar[FigureKind] = FigureKind.MERMAID

    def text(self) -> str:
        blocks = [self.description, *(f"```mermaid\n{p.mermaid}\n```" for p in self.panels)]
        if self.note is not None:
            blocks.append(self.note)
        return "\n\n".join(blocks)

    def is_drawable(self) -> bool:
        return bool(self.panels)

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "description": self.description,
            "panels": [panel.to_json() for panel in self.panels],
            "note": self.note,
        }


@dataclass(frozen=True)
class AddedSignature:
    """新規の宣言。変更前が無いので old を持たない。"""

    name: str
    place: str
    new: str

    change: ClassVar[SignatureChange] = SignatureChange.ADDED

    def old_text(self) -> str:
        return ADDED_SIGNATURE_TEXT

    def new_text(self) -> str:
        return self.new

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "place": self.place, "change": self.change, "new": self.new}


@dataclass(frozen=True)
class DeletedSignature:
    """削除した宣言。変更後が無いので new を持たない。"""

    name: str
    place: str
    old: str

    change: ClassVar[SignatureChange] = SignatureChange.DELETED

    def old_text(self) -> str:
        return self.old

    def new_text(self) -> str:
        return DELETED_SIGNATURE_TEXT

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "place": self.place, "change": self.change, "old": self.old}


@dataclass(frozen=True)
class ChangedSignature:
    """シグネチャが変わった宣言。"""

    name: str
    place: str
    old: str
    new: str

    change: ClassVar[SignatureChange] = SignatureChange.CHANGED

    def old_text(self) -> str:
        return self.old

    def new_text(self) -> str:
        return self.new

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "place": self.place,
            "change": self.change,
            "old": self.old,
            "new": self.new,
        }


@dataclass(frozen=True)
class MovedSignature:
    """別のファイルへ移しただけの宣言。シグネチャは移す前と同じなので text は一つ。"""

    name: str
    old_place: str
    new_place: str
    text: str

    change: ClassVar[SignatureChange] = SignatureChange.MOVED

    @property
    def place(self) -> str:
        return self.new_place

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "place": self.new_place,
            "change": self.change,
            "old_place": self.old_place,
            "text": self.text,
        }


SignatureEntry = AddedSignature | DeletedSignature | ChangedSignature | MovedSignature


def _signature_block(entry: SignatureEntry) -> str:
    if isinstance(entry, MovedSignature):
        return (
            f"#### {entry.name}（移した（{entry.old_place} → {entry.new_place}）、"
            f"シグネチャは同じ）\n\n```\n{entry.text}\n```"
        )
    return (
        f"#### {entry.name}（{entry.place}）\n\n```\n"
        f"- 変更前: {entry.old_text()}\n"
        f"+ 変更後: {entry.new_text()}\n```"
    )


@dataclass(frozen=True)
class SignatureFigure:
    """シグネチャの変更前後の表"""

    entries: tuple[SignatureEntry, ...]

    kind: ClassVar[FigureKind] = FigureKind.SIGNATURES

    def text(self) -> str:
        if not self.entries:
            return NO_SIGNATURE_CHANGE_TEXT
        blocks = [_signature_block(e) for e in self.entries]
        return "\n\n".join(blocks)

    def is_drawable(self) -> bool:
        return bool(self.entries)

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "entries": [entry.to_json() for entry in self.entries],
        }


class RowChange(StrEnum):
    """表の行の変更の別。値は JSON の印にそのまま使う。"""

    UNCHANGED = ""
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


@dataclass(frozen=True)
class TableRow:
    cells: tuple[str, ...]
    change: RowChange


@dataclass(frozen=True)
class TableFigure:
    """Markdown の表でできた図。rows が空のときは描くものが無い。"""

    description: str
    header: tuple[str, ...]
    rows: tuple[TableRow, ...]
    note: str | None = None

    kind: ClassVar[FigureKind] = FigureKind.TABLE

    def text(self) -> str:
        table = markdown_table(
            self.header, tuple(row.cells for row in self.rows), ["cell"] * len(self.header)
        )
        blocks = [self.description, table]
        if self.note is not None:
            blocks.append(self.note)
        return "\n\n".join(blocks)

    def is_drawable(self) -> bool:
        return bool(self.rows)

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "description": self.description,
            "header": list(self.header),
            "rows": [{"cells": list(row.cells), "change": row.change} for row in self.rows],
            "note": self.note,
        }


Figure = TreeFigure | SequenceFigure | MermaidFigure | SignatureFigure | TableFigure
