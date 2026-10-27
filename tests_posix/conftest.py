"""POSIX だけで意味を持つ試験の置き場。既定の testpaths の外に置く。

macOS と Linux のゲート（Makefile の test）だけが走らせる。
Windows のゲート（justfile の test）は走らせない。
置き場の違いで走る OS が決まるため、条件付きの skip は使わない。tests_windows と対になる。
"""

from test_run_record import review  # noqa: F401 pytest が fixture を名前で引くため import が要る
from test_security import project  # noqa: F401 pytest が fixture を名前で引くため import が要る

pytest_plugins = ["shared_fixtures", "language_plugin_support"]
