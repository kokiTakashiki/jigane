"""vulture が「使われていない」と誤って報告する名前の一覧。一件ごとに、使われている経路を書く。

vulture は名前の出現だけを数える。文字列で引かれる名前、外のライブラリや pytest が名前で呼ぶもの、
公開の契約として残すものは、ここに名前を載せて数えさせる。本当に使われないコードはここへ
載せず消す。
"""

from typing import Any

_: Any = None

# --- 保存した JSON の鍵に対応する dataclass の項目。from_json と to_dict が文字列の鍵で読み書きする ---
_.html_url  # pull_request_context/bots.py の BotComment。GitHub の API の応答の鍵 comment["html_url"] と同じ名前で持つ
_.record_kind  # pipeline.py の PolicyGroupRun。_OWN_PLAIN_KEYS の文字列で読み書きする

# --- getattr や鍵の文字列が名前で引く ---
_.total_tokens  # usage.py の Measures。_field(UsageField.TOTAL_TOKENS) が getattr(row, "total_tokens") で引く

# --- 文字列の値から復元する列挙型の項目。Enum(値) が引くので、コードに名前が現れない ---
_.SOLID_CROSS  # mermaid.py の SequenceArrow。SequenceArrow(message["arrow"]) が原文の矢印から引く
_.DOTTED_CROSS  # 同上
_.DOTTED_ASYNC  # 同上
_.SOLID_OPEN  # 同上
_.DOTTED_OPEN  # 同上
_.TOP_BOTTOM  # mermaid.py の FlowDirection。FlowDirection(found["direction"]) が原文の向きから引く
_.BOTTOM_TOP  # 同上
_.RIGHT_LEFT  # 同上
_.DOCS  # findings/records.py の ToolBasis。ToolBasis(raw["basis"]) が保存した文字列から引く
_.RUN  # 同上
_.MEDIUM  # groups/definitions.py の Effort。claude の --effort が取りうる値の全体を列挙で持つ
_.XHIGH  # 同上

# --- 外のライブラリが名前で呼ぶ ---
_.pytest_xdist_auto_num_workers  # pytest-xdist のフック。プラグインが名前で呼ぶ
_.whitespace_split  # scripts/check_no_git_stash.py。shlex が属性の値を読んで語の切り方を変える
_.commenters  # 同上。shlex が属性の値を読んで注釈の記号を決める
_.model_config  # skills/output_models.py。pydantic がクラスの変数 model_config を名前で読む
_.declaration_ref  # llm_runs/finding_model.py。pydantic が入力の鍵と欄を名前で突き合わせる
_.judgment_region  # 同上
_._check_conditions  # 同上。pydantic が model_validator の付いた関数を検証の時に呼ぶ
_.pytest_plugins  # pytest が conftest の変数 pytest_plugins を読んでプラグインを読み込む

# --- pytest の fixture。試験関数の引数の名前で注入されるので、コードに呼び出しが現れない ---
_._jigane_home  # tests/conftest.py の autouse。全試験のデータの置き場を一時の場所へ向ける
_._language_states  # tests/conftest.py の autouse。言語の状態を試験ごとに捨てる
_._terminal_with_consent  # tests/conftest.py の autouse。run の同意の確認に y と答える偽物を与える
_.real_read_answer  # tests/conftest.py。引数の名前で受ける試験が、偽物を本物へ戻す
_.empty_store  # test_builtin_grammar_missing.py。引数の名前で受ける
_._fresh_query_cache  # test_language_data_places.py の autouse。問い合わせの記憶を試験ごとに捨てる
_.empty_path  # test_missing_tool_guidance.py。引数の名前で受ける

# --- 構文解析の試験の入力。Python のコードとして書くが、試験は木を読むだけで import も呼び出しもしない ---
_.boot  # tests/fixtures/sequence/python/pkg/__init__.py。シーケンス図の抽出の入力
_.Point  # tests/fixtures/sequence/python/pkg/shapes.py。同上
_.Inner  # 同上

# --- この木の中からは呼ばれない。木の外の運用のスクリプトが読むために残す ---
_.estimate_basis_row_labels  # policy_runs.py。見積もりの過去実額に数える行の名前を返す
_.finding_index  # finding_model.py の MergedGroup。pydantic が出力の検証で名前を引く

# --- 外のライブラリが属性の名前で読む設定 ---
_.preserve_quotes  # scripts/export_public.py。ruamel.yaml の YAML が引用符の書き方を保つ設定

# --- 段 1 で足した項目の一覧。段 2 の試験と段 4 の SECURITY.md の表の生成が読む ---
_.channel  # llm_runs/claude_inputs.py の InputSpec。段 2 以降の試験と生成が読む
_.user_can_provide  # llm_runs/claude_inputs.py の InputSpec。段 4 の表の生成が読む
_.user_can_provide_reason  # llm_runs/claude_inputs.py の InputSpec。段 4 の表の生成が読む
_.TOOL_PERMISSIONS  # llm_runs/claude_inputs.py の ClaudeInput。段 3 の記録の突き合わせが読む
_.OUTPUT_SCHEMA  # 同上
_.RUN_LIMITS  # 同上
_.MCP_SERVERS  # 同上
_.ADDED_ENVIRONMENT  # 同上
_.REMOVED_ENVIRONMENT  # 同上
_.INHERITED_ENVIRONMENT  # 同上
_.WORKING_DIRECTORY  # 同上
