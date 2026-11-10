"""保存物のファイルの拡張子の一覧。設計書の読み取りとコードの読み取りが同じ一覧を引く。"""

# 先頭の `.` を除いた綴り
STORED_FILE_SUFFIXES = frozenset({"json", "jsonl", "ndjson", "csv", "tsv", "toml", "yaml", "yml"})
