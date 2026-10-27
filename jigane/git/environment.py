"""親の git（hook や IDE）から渡り、起こす git の向き先を狂わせる環境変数。

入口の生成器（scripts/generate_entrypoints.py）は標準ライブラリだけで動くため、
このファイルをパスから読む。jigane の他のモジュールを import しない。
"""

GIT_LOCATION_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")
