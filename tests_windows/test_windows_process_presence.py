"""Windows ではプロセスの有無を確かめられず UNKNOWN になること。

既定の testpaths の外に置き、Windows の CI の job だけが明示して走らせる。
"""

import os

from jigane.skills.run_record import ProcessPresence, process_presence


def test_process_presence_is_unknown_on_windows() -> None:
    assert process_presence(os.getpid()) is ProcessPresence.UNKNOWN
