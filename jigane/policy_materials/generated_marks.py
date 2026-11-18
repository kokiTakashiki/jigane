"""head の木の `linguist-generated` の印を読み、変更ファイルの出どころを決める。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from jigane.git import process
from jigane.git.command_line import GitSubcommand, build_git_command
from jigane.git.revision import CommitSha, parse_commit_sha
from jigane.judgment_map.configuration import Config, is_excluded, load_config
from jigane.policy_materials.gitattributes_marks import AttributeLines, read_attribute_lines

MARK_ATTRIBUTE = "linguist-generated"
# check-attr に渡すパスの上限。超えた分は検査せず UNCHECKED にする
MAX_CHECKED_PATHS = 2000
_SET = "set"


class GeneratedOrigin(StrEnum):
    """変更ファイルが生成物と見なされる出どころ。"""

    EXCLUDE = "exclude"
    LINGUIST_GENERATED = MARK_ATTRIBUTE
    NONE = "none"


class MarkState(StrEnum):
    MARKED = "marked"
    UNMARKED = "unmarked"
    UNCHECKED = "unchecked"


@dataclass(frozen=True)
class MarksRead:
    """印を読めた結果。上限で検査しなかったパスは UNCHECKED で持つ。"""

    states: Mapping[str, MarkState]

    def state(self, path: str) -> MarkState | None:
        """読んだ対象に無いパスは None。"""
        return self.states.get(path)

    @property
    def unchecked_count(self) -> int:
        return sum(state is MarkState.UNCHECKED for state in self.states.values())

    @property
    def has_marked(self) -> bool:
        return any(state is MarkState.MARKED for state in self.states.values())


@dataclass(frozen=True)
class MarksUnreadable:
    """git の失敗か出力の割れで、どのファイルの印も分からない結果。"""


type MarkReading = MarksRead | MarksUnreadable


def _parse_triples(output: bytes, checked: Sequence[str]) -> dict[str, MarkState] | None:
    """`-z` の出力を `<パス> NUL <属性> NUL <値> NUL` の三つ組で解く。割れなければ None。"""
    fields = output.split(b"\0")
    if not fields or fields[-1] != b"":
        return None
    fields.pop()
    if len(fields) != 3 * len(checked):
        return None
    expected = set(checked)
    states: dict[str, MarkState] = {}
    for index in range(0, len(fields), 3):
        try:
            path, attribute, value = (part.decode("utf-8") for part in fields[index : index + 3])
        except UnicodeDecodeError:
            return None
        if attribute != MARK_ATTRIBUTE or path not in expected:
            return None
        states[path] = MarkState.MARKED if value == _SET else MarkState.UNMARKED
    return states


def read_marks(root: Path, head: CommitSha, paths: Sequence[str]) -> MarkReading:
    """head のコミットの木で、paths の `linguist-generated` の状態を読む。

    作業木と index は読まない。`.git/info/attributes` は `--source` を渡しても効く。
    """
    unique = list(dict.fromkeys(paths))
    checked = unique[:MAX_CHECKED_PATHS]
    unchecked = unique[MAX_CHECKED_PATHS:]
    if checked:
        stdin = b"".join(path.encode("utf-8") + b"\0" for path in checked)
        try:
            # git 2.54.0 の check-attr は、--source のコミットが無くても空の木として 0 で終わる。
            # 先に在ることを確かめないと、読めなかったことが UNMARKED に潰れる
            process.run_command_bytes(
                build_git_command(GitSubcommand.CAT_FILE, options=["-e"], revisions=[head]),
                cwd=root,
            )
            output = process.run_command_bytes(
                build_git_command(
                    GitSubcommand.CHECK_ATTR,
                    options=["--source", str(head), "-z", "--stdin"],
                    fixed_values=[MARK_ATTRIBUTE],
                ),
                cwd=root,
                stdin=stdin,
            )
        except process.CommandError as error:
            if error.missing_executable:
                raise
            # git 2.40 未満の --source 未対応もここに来る。失敗の文面は資料に載せない
            return MarksUnreadable()
        states = _parse_triples(output, checked)
        if states is None:
            return MarksUnreadable()
    else:
        states = {}
    states.update(dict.fromkeys(unchecked, MarkState.UNCHECKED))
    return MarksRead(states)


def generated_origins(
    paths: Sequence[str], reading: MarkReading, config: Config
) -> dict[str, GeneratedOrigin]:
    """exclude に合うファイルは EXCLUDE、合わずに印のあるファイルは LINGUIST_GENERATED にする。"""
    origins: dict[str, GeneratedOrigin] = {}
    for path in paths:
        if is_excluded(path, config):
            origins[path] = GeneratedOrigin.EXCLUDE
        elif isinstance(reading, MarksRead) and reading.state(path) is MarkState.MARKED:
            origins[path] = GeneratedOrigin.LINGUIST_GENERATED
        else:
            origins[path] = GeneratedOrigin.NONE
    return origins


@dataclass(frozen=True)
class GeneratedHint:
    """変更後の木の生成物の印。reading が None なら head を持たず、印を読んでいない。"""

    reading: MarkReading | None
    # 出どころが linguist-generated の、変更後に残るパス
    generated: frozenset[str]
    # head の木の .gitattributes の印の行。読んでいないか git が失敗したら None
    attribute_lines: AttributeLines | None = None


NO_GENERATED_HINT = GeneratedHint(None, frozenset())


def generated_hint(root: Path, head: str, paths: Sequence[str], base_tree: Path) -> GeneratedHint:
    """head のコミットの木から、変更後に残る変更ファイル paths の印を一度だけ読む。

    root は履歴を読む git の場所、base_tree は設定を読む変更前の木。
    """
    try:
        sha = parse_commit_sha("head", head)
    except ValueError:
        # 保存した head が SHA の形でなければ、check-attr に渡さず読めなかった形にする
        return GeneratedHint(MarksUnreadable(), frozenset())
    reading = read_marks(root, sha, paths)
    # 変更を出す側が設定を緩められないよう、差分と同じく変更前の木の設定で決める
    origins = generated_origins(paths, reading, load_config(base_tree))
    generated = frozenset(
        path for path, origin in origins.items() if origin is GeneratedOrigin.LINGUIST_GENERATED
    )
    return GeneratedHint(reading, generated, read_attribute_lines(root, sha))
