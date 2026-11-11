import os
import sys

__version__ = "0.1.8"


def _drop_working_directory_from_import_path() -> None:
    """作業ディレクトリを import の道から外す。

    レビュー対象のリポジトリのファイルが標準のモジュールを覆わないようにする。

    PYTHONPATH の空の項目と `python -m` の先頭の項目は作業ディレクトリを指し、
    PYTHONSAFEPATH でも空の項目は外れない。ここより前に import が走ると、
    `email.py` のような名前のファイルが標準のモジュールより先に読まれる。
    そのため、起動時に読み込み済みの `sys` と `os` だけを使い、`pathlib` などは import しない。
    """
    try:
        working_directory = os.path.realpath(os.getcwd())  # noqa: PTH109 -- pathlib は import しない
    except FileNotFoundError:
        # 作業ディレクトリが消えていれば、そこを指す項目から読まれるファイルも無い
        return
    sys.path[:] = [
        entry for entry in sys.path if entry != "" and os.path.realpath(entry) != working_directory
    ]


_drop_working_directory_from_import_path()
