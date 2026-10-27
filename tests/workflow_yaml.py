"""ワークフローと設定の YAML を、行ではなく構文木で読む試験の補助。

行ごとの正規表現や部分一致では、引用やフロー形式やコメントの中の文字列を取り違える。
構文木に読めば、キーと値とコメントを別々に扱える。
"""

from collections.abc import Iterator

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


def load_yaml(text: str) -> CommentedMap:
    """コメントを保つローダで読む。コメントの位置を見る試験と、値だけを見る試験が共用する。"""
    tree = YAML(typ="rt").load(text)
    assert isinstance(tree, CommentedMap)
    return tree


def job_map(tree: CommentedMap) -> CommentedMap:
    jobs = tree["jobs"]
    assert isinstance(jobs, CommentedMap)
    return jobs


def uses_holders(
    tree: CommentedMap,
) -> Iterator[tuple[CommentedMap, str, CommentedSeq | None, int]]:
    """`uses` を持つ対応表を、(対応表、値、所属する steps の列、列の中の位置) で返す。

    対象は jobs.*.uses（再利用ワークフロー）、jobs.*.steps[*].uses、runs.steps[*].uses
    （composite action）。
    """
    containers: list[CommentedMap] = [*job_map(tree).values()] if "jobs" in tree else []
    runs = tree.get("runs")
    if isinstance(runs, CommentedMap):
        containers.append(runs)
    for container in containers:
        if "uses" in container:
            yield container, str(container["uses"]), None, -1
        steps = container.get("steps")
        if not isinstance(steps, CommentedSeq):
            continue
        for index, step in enumerate(steps):
            if isinstance(step, CommentedMap) and "uses" in step:
                yield step, str(step["uses"]), steps, index


def uses_values(tree: CommentedMap) -> list[str]:
    return [value for _, value, _, _ in uses_holders(tree)]


def trailing_comment(holder: CommentedMap, steps: CommentedSeq | None, index: int) -> str:
    """`uses` と同じ行の末尾のコメントを返す。無ければ空。

    ブロック形式ではキー `uses` に、フロー形式（`{uses: ...} # v4`）では steps の列の要素に付く。
    次の行から始まるコメントは同じ行ではないので、値が `#` で始まるものだけを数える。
    """
    candidates: list[object] = []
    key_slots = holder.ca.items.get("uses")
    if key_slots is not None:
        candidates.append(key_slots[2])
    if steps is not None:
        item_slots = steps.ca.items.get(index)
        if item_slots is not None:
            candidates.append(item_slots[0])
    for token in candidates:
        value = getattr(token, "value", "")
        if value.startswith("#"):
            return value[1:].strip()
    return ""
