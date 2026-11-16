"""公開の写しの入口（entrypoints.toml）から外す非公開の作業の定義。"""

from typing import NamedTuple

import tomlkit

# 写しの作業から外す行の目印。開発のゲートの設定で、写しは .git が無く、
# 利用者の git config にも書かせないため
PRIVATE_COMMAND_MARKER = "claude.gateCommand"
# 公開の写しの入口から外す作業の名前。生成器は公開版の除外を知らないので、ここだけが持つ
PRIVATE_TASKS = ("export-public", "public-sync")


class DroppedTasks(NamedTuple):
    text: str
    names: list[str]


def drop_private_tasks(text: str) -> DroppedTasks:
    """entrypoints.toml の文面から、PRIVATE_TASKS に名前のある [[task]] を外す。

    外した作業の名前も返す。

    残す作業の commands からも、PRIVATE_COMMAND_MARKER を含む開発用の行を外す。
    """
    document = tomlkit.parse(text)
    # [[task]] が無い文書は空の列として扱い、外すものが無いまま返す
    tasks = document.get("task") or []
    dropped: list[str] = []
    # 後ろから外し、これから見る要素の添字を動かさない
    for index in reversed(range(len(tasks))):
        name = tasks[index].get("name")
        if name in PRIVATE_TASKS:
            dropped.insert(0, name)
            del tasks[index]
            continue
        commands = tasks[index].get("commands")
        # commands を持たない作業は外す行が無いので、そのまま残す
        if commands is None:
            continue
        for position in reversed(range(len(commands))):
            if PRIVATE_COMMAND_MARKER in commands[position]:
                del commands[position]
    return DroppedTasks(tomlkit.dumps(document), dropped)
