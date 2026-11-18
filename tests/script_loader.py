"""scripts/ のスクリプトを、試験が名前で読み込む補助。"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def import_script(name: str, *, register: bool = False) -> ModuleType:
    """scripts/<name>.py を新しいモジュールとして読む。

    register が真のとき、実行の前に sys.modules へ登録する。dataclass などが自分のモジュールを
    sys.modules から引くスクリプトに使う。
    """
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
