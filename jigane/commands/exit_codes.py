"""終了コード。値は要件 4.10 節に定める。"""

EXIT_OK = 0
EXIT_GATE_VIOLATION = 1
EXIT_PROCESSING_FAILURE = 2
EXIT_INCOMPLETE_COVERAGE = 3
# sysexits.h の EX_USAGE。引数の誤りで、何も起動していない
EXIT_USAGE = 64
# 見積もりが上限を超えて、起動の前に止めた。何も走らせていないので、
# 欠けのある回の 3 でなく処理の失敗の側に置く
EXIT_ESTIMATE_OVER_LIMIT = EXIT_PROCESSING_FAILURE
# シェルの慣行（128 + SIGINT）に合わせた値
EXIT_INTERRUPTED = 130
