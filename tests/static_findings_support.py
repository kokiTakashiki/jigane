"""静的検査の指摘の取り込みと表示の試験が共有する、SARIF ファイルと取り込み要求の組み立て。"""

from pathlib import Path

from sarif_support import log, result, run, write

from jigane.static_analysis.intake import SarifRequest, sarif_request

MESSAGE = "UNIQUE-FINDING-MESSAGE-7731"


def sarif_file(root: Path, name: str, *, revision: str | None, level: str = "error") -> Path:
    extra: dict[str, object] = {}
    if revision is not None:
        extra["versionControlProvenance"] = [{"revisionId": revision}]
    document = log(run([result("edit.py", 2, MESSAGE, level=level)], **extra))
    return write(root.parent, name, document)


def sarif_request_for(root: Path, path: Path) -> SarifRequest | None:
    return sarif_request([path], None, root)
