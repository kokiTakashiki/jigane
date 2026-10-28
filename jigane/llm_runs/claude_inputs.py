"""jigane が Claude Code に渡す情報の種類の正で、段 2〜4 の試験と SECURITY.md の表がこれを使う。"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import NamedTuple

from jigane.groups.definitions import GroupId
from jigane.languages.grammars import CACHE_ENV
from jigane.llm_runs.claude import (
    CLAUDEAI_MCP_SWITCH,
    NONESSENTIAL_TRAFFIC_SWITCH,
    REVIEWER_STRIPPED_ENVIRONMENT_VARIABLES,
)


class Channel(StrEnum):
    PROMPT = "prompt"
    ARGV = "argv"
    ENVIRONMENT = "environment"
    CWD = "cwd"


class Role(StrEnum):
    """渡る先の役。値は skills/adversarial.py の _ROLE_ITEMS と各群の雛形の名前と同じ。"""

    MACRO_DIAGRAM = "macro_diagram"
    MACRO_DESIGN = "macro_design"
    MICRO = "micro"
    SECURITY = "security"
    REBUTTAL = "rebuttal"
    ALTERNATIVE = "alternative"
    DEFENSE = "defense"
    JUDGE_REBUTTAL = "judge_rebuttal"
    JUDGE_ALTERNATIVE = "judge_alternative"
    BUNDLE_DISCLOSED = "bundle_disclosed"
    THEMES = "themes"


class GroupRole(NamedTuple):
    group: GroupId
    role: Role


@dataclass(frozen=True)
class InputSpec:
    channel: Channel
    content: str
    # 「jigane/…py:シンボル名」。シンボルはファイルの直下かクラスの中の def、class、代入の名前
    source: tuple[str, ...]
    roles: tuple[GroupRole, ...]
    user_can_provide: bool
    user_can_provide_reason: str


_MACRO_DIAGRAM = (GroupRole(GroupId.MACRO, Role.MACRO_DIAGRAM),)
_MACRO_DESIGN = (GroupRole(GroupId.MACRO, Role.MACRO_DESIGN),)
_MICRO = (GroupRole(GroupId.MICRO, Role.MICRO),)
_SECURITY = (GroupRole(GroupId.SECURITY, Role.SECURITY),)
_FOLLOWERS = (
    Role.REBUTTAL,
    Role.ALTERNATIVE,
    Role.DEFENSE,
    Role.JUDGE_REBUTTAL,
    Role.JUDGE_ALTERNATIVE,
    Role.BUNDLE_DISCLOSED,
)
_MICRO_FOLLOWERS = tuple(GroupRole(GroupId.MICRO, role) for role in (*_FOLLOWERS, Role.THEMES))
_SECURITY_FOLLOWERS = tuple(GroupRole(GroupId.SECURITY, role) for role in _FOLLOWERS)
_VERIFIERS = tuple(
    GroupRole(group, role)
    for group in (GroupId.MICRO, GroupId.SECURITY)
    for role in (
        Role.REBUTTAL,
        Role.ALTERNATIVE,
        Role.DEFENSE,
        Role.JUDGE_REBUTTAL,
        Role.JUDGE_ALTERNATIVE,
    )
)
_ALL = (
    *_MACRO_DIAGRAM,
    *_MACRO_DESIGN,
    *_MICRO,
    *_SECURITY,
    *_MICRO_FOLLOWERS,
    *_SECURITY_FOLLOWERS,
)


# 環境変数が Claude Code のプロセスに置かれるだけで、モデルへは渡らないことの説明。
# 道具が読み取りだけで --restricted が付くことは tests/test_claude_arguments.py が守る
class ClaudeInput(Enum):
    # --- 標準入力（PROMPT） ---
    CHANGE_DIFF = InputSpec(
        Channel.PROMPT,
        "変更の差分（変更前と変更後の行）。機能レビューでは変更前は空の旨の固定文。"
        "差分に添える設定ファイルの冒頭（Makefile、justfile、pyproject の最大 40 行）を含む",
        (
            "jigane/skills/macro.py:changes_prompt_text",
            "jigane/skills/macro.py:verifier_changes_text",
            "jigane/skills/sharded.py:build_shard_prompt",
            "jigane/skills/sharded.py:_changes_with_config_heads",
            "jigane/skills/config_head.py:config_heads_section",
        ),
        (*_MICRO, *_SECURITY, *_VERIFIERS),
        user_can_provide=True,
        user_can_provide_reason="git diff と同じ内容",
    )
    TARGET_FILES = InputSpec(
        Channel.PROMPT,
        "レビューの対象のファイルの一覧と本文。群によって中身が違う。"
        "micro と security は担当のファイルの本文（差分に添える設定ファイルの冒頭を含む）、"
        "macro はレビューの範囲とファイルの一覧と、そこから描いた図だけで本文は渡らない",
        (
            "jigane/skills/macro.py:build_diagram_prompt",
            "jigane/skills/sharded.py:build_shard_prompt",
            "jigane/skills/sharded.py:_changes_with_config_heads",
            "jigane/skills/config_head.py:config_heads_section",
            "jigane/skills/policy_regex.py:shard_checks",
        ),
        (*_MACRO_DIAGRAM, *_MICRO, *_SECURITY),
        user_can_provide=True,
        user_can_provide_reason="作業ディレクトリのファイル",
    )
    BASE_TREE = InputSpec(
        Channel.PROMPT,
        "変更前の木の中身。図、判断の地図、同じ形の箇所、技術選定を通して入る",
        (
            "jigane/skills/macro.py:policy_sections",
            "jigane/pull_request_context/fetching.py:extract_tree",
            "jigane/reviews/lifecycle.py:_build_diff_tree",
        ),
        _MACRO_DIAGRAM,
        user_can_provide=True,
        user_can_provide_reason=(
            "変更前のコミットの中身で git show で取れる。置き場は作業ディレクトリの外"
        ),
    )
    DECLARATION = InputSpec(
        Channel.PROMPT,
        "変更の意図（PR の題と本文、--intent、コミットのメッセージ、設計書の宣言）と、"
        "PR の本文が参照する文書のパス（パスだけで、文書の本文は載せない）",
        (
            "jigane/skills/macro.py:declaration_prompt_text",
            "jigane/skills/macro_design.py:build_design_prompt",
            "jigane/pull_request_context/background.py:find_documents",
        ),
        (*_MACRO_DIAGRAM, *_MACRO_DESIGN),
        user_can_provide=True,
        user_can_provide_reason="gh と git log で利用者も渡せる",
    )
    PAST_COMMITS = InputSpec(
        Channel.PROMPT,
        "同じファイルに触れた過去のコミットの短縮 SHA と題",
        (
            "jigane/skills/macro.py:background_prompt_text",
            "jigane/pull_request_context/background.py:find_past_changes",
        ),
        _MACRO_DIAGRAM,
        user_can_provide=True,
        user_can_provide_reason="git log で取れるが、どれを足すかは jigane が選ぶ",
    )
    PAST_PULL_REQUESTS = InputSpec(
        Channel.PROMPT,
        "同じファイルに触れた過去の PR の番号と題",
        (
            "jigane/skills/macro.py:background_prompt_text",
            "jigane/pull_request_context/background.py:find_past_pull_requests",
        ),
        _MACRO_DIAGRAM,
        user_can_provide=True,
        user_can_provide_reason="gh で取れるが、どれを足すかは jigane が選ぶ",
    )
    CODEOWNERS = InputSpec(
        Channel.PROMPT,
        "変更したパスの持ち主",
        (
            "jigane/skills/macro.py:background_prompt_text",
            "jigane/pull_request_context/background.py:find_owners",
        ),
        _MACRO_DIAGRAM,
        user_can_provide=True,
        user_can_provide_reason="リポジトリの CODEOWNERS ファイル",
    )
    BOT_COMMENTS = InputSpec(
        Channel.PROMPT,
        "レビューボットのコメント（ログイン名、位置、本文）",
        (
            "jigane/skills/macro.py:bot_findings",
            "jigane/pull_request_context/bots.py:fetch_bot_comments",
        ),
        _MACRO_DIAGRAM,
        user_can_provide=True,
        user_can_provide_reason="gh で取れるが、整形は jigane",
    )
    JUDGMENT_MAP = InputSpec(
        Channel.PROMPT,
        "判断の地図（変更の種類、判断の要否、箇所）",
        (
            "jigane/policy_materials/changed_files.py:materials_sections",
            "jigane/skills/policy_figures.py:prompt_figure_texts",
        ),
        _MACRO_DIAGRAM,
        user_can_provide=True,
        user_can_provide_reason="利用者自身のコードと変更から jigane が整形したもの",
    )
    TECHNOLOGY_LIST = InputSpec(
        Channel.PROMPT,
        "技術の追加と削除、依存の宣言の変化",
        (
            "jigane/skills/macro.py:policy_sections",
            "jigane/technology_list/materials.py:short_section_text",
        ),
        _MACRO_DIAGRAM,
        user_can_provide=True,
        user_can_provide_reason="利用者自身の依存の宣言から jigane が整形したもの",
    )
    SAME_SHAPE_LOCATIONS = InputSpec(
        Channel.PROMPT,
        "同じ形の箇所（消した行と同じ行が残る箇所、重なる塊）の宣言の抜粋",
        (
            "jigane/skills/macro.py:policy_sections",
            "jigane/skills/policy_repeats.py:repeats_text",
        ),
        _MACRO_DIAGRAM,
        user_can_provide=True,
        user_can_provide_reason="利用者自身のコードから jigane が整形したもの",
    )
    DESIGN_DOC = InputSpec(
        Channel.PROMPT,
        "設計書の本文、節の構造、名指しの照合、選んだファイルの対応と、そのファイルから描いたいまの形の図",
        (
            "jigane/skills/macro_design.py:design_materials_text",
            "jigane/skills/sharded.py:_design_insert",
        ),
        (*_MACRO_DESIGN, *_MICRO, *_SECURITY),
        user_can_provide=True,
        user_can_provide_reason="作業ディレクトリのファイル。構造と照合は jigane が付ける",
    )
    REVIEW_HISTORY = InputSpec(
        Channel.PROMPT,
        "前の回の指摘と利用者の異議、他のレビューの指摘",
        (
            "jigane/skills/history.py:history_text",
            "jigane/skills/history.py:shard_history_text",
            "jigane/skills/macro.py:history_text",
        ),
        (*_MACRO_DIAGRAM, *_MACRO_DESIGN, *_MICRO),
        user_can_provide=True,
        user_can_provide_reason="利用者自身の前のレビューと異議から jigane が整形したもの",
    )
    POLICY_CONCERNS = InputSpec(
        Channel.PROMPT,
        "方針の群の指摘を実装の群へ渡した懸念",
        (
            "jigane/skills/sharded.py:shard_concerns",
            "jigane/findings/records.py:concerns_section",
        ),
        _MICRO,
        user_can_provide=True,
        user_can_provide_reason="利用者自身のレビューの指摘から jigane が整形したもの",
    )
    ROLE_OUTPUTS = InputSpec(
        Channel.PROMPT,
        "同じレビューの前の段の出力（指摘の候補と、その検証）",
        (
            "jigane/skills/adversarial.py:_indexed",
            "jigane/skills/adversarial.py:_verify_split",
            "jigane/skills/triage.py:build_themes_prompt",
            "jigane/skills/bundle_disclosed.py:build_prompt",
        ),
        (
            *_VERIFIERS,
            GroupRole(GroupId.MICRO, Role.BUNDLE_DISCLOSED),
            GroupRole(GroupId.MICRO, Role.THEMES),
            GroupRole(GroupId.SECURITY, Role.BUNDLE_DISCLOSED),
        ),
        user_can_provide=True,
        user_can_provide_reason="利用者自身のレビューの前の段の出力から jigane が整形したもの",
    )
    SOURCE_EXCERPTS = InputSpec(
        Channel.PROMPT,
        "検証の段で確かめた位置の抜粋。jigane が作業ディレクトリから読み直して渡す",
        (
            "jigane/skills/adversarial.py:_excerpt",
            "jigane/skills/adversarial.py:_checked_excerpts",
            "jigane/llm_runs/claude.py:is_secret_file",
        ),
        (
            GroupRole(GroupId.MICRO, Role.JUDGE_REBUTTAL),
            GroupRole(GroupId.SECURITY, Role.JUDGE_REBUTTAL),
        ),
        user_can_provide=True,
        user_can_provide_reason="作業ディレクトリのファイル。秘密のファイル名は除く",
    )
    MCP_GUIDE = InputSpec(
        Channel.PROMPT,
        "利用者の MCP の使い分けの TOML と、使える MCP の名前の注記",
        (
            "jigane/skills/sharded.py:mcp_note",
            "jigane/skills/sharded.py:mcp_guide_note",
            "jigane/skills/mcp.py:read_guide",
        ),
        _SECURITY,
        user_can_provide=True,
        user_can_provide_reason="利用者自身が jigane に渡した設定を jigane が整形したもの",
    )
    INSTRUCTIONS = InputSpec(
        Channel.PROMPT,
        "jigane の雛形の固定の文で、利用者のデータではない（観点、基準、範囲の規則、"
        "捨てる理由の表、担当のファイルの番号など）",
        (
            "jigane/skills/shared_rules.py:prompt_text",
            "jigane/skills/adversarial.py:render_prompt",
            "jigane/skills/macro_shared.py:prompt_parts",
        ),
        _ALL,
        user_can_provide=True,
        user_can_provide_reason="公開の雛形の文。利用者のデータを含まない",
    )
    # --- argv、環境変数、cwd ---
    # 渡す道具が読み取りだけであることは、tests/test_claude_arguments.py の
    # test_every_launch_is_restricted_read_only_and_never_skips_permissions が守る
    TOOL_PERMISSIONS = InputSpec(
        Channel.ARGV,
        "--restricted、--tools、--settings の秘密のファイル名の deny と"
        "作業ディレクトリの外の読み取りの拒否、--strict-mcp-config",
        (
            "jigane/llm_runs/claude.py:build_command",
            "jigane/llm_runs/claude.py:SECRET_FILE_PATTERNS",
        ),
        _ALL,
        user_can_provide=True,
        user_can_provide_reason="利用者も渡せる。値の組は jigane のもの",
    )
    OUTPUT_SCHEMA = InputSpec(
        Channel.ARGV,
        "--output-format json と --json-schema（段ごとの出力の形）",
        (
            "jigane/llm_runs/claude.py:_schema_for_cli",
            "jigane/skills/output_models.py:json_schema_of",
        ),
        _ALL,
        user_can_provide=True,
        user_can_provide_reason="jigane の固定の出力モデルから作る文",
    )
    RUN_LIMITS = InputSpec(
        Channel.ARGV,
        "--model、--effort、--max-turns、--max-budget-usd",
        (
            "jigane/llm_runs/claude.py:build_command",
            "jigane/llm_runs/defaults.py:resolve_run_defaults",
        ),
        _ALL,
        user_can_provide=True,
        user_can_provide_reason="利用者も渡せる",
    )
    MCP_SERVERS = InputSpec(
        Channel.ARGV,
        "--mcp-config の一時ファイル（利用者の mcp.json から選んだサーバの定義）と --allowedTools",
        (
            "jigane/llm_runs/claude.py:build_command",
            "jigane/skills/mcp.py:mcp_config_path",
            "jigane/skills/security.py:run_security",
        ),
        _SECURITY,
        user_can_provide=True,
        user_can_provide_reason="利用者の MCP の設定。認証情報を含みうる",
    )
    ADDED_ENVIRONMENT = InputSpec(
        Channel.ENVIRONMENT,
        "ENABLE_CLAUDEAI_MCP_SERVERS=false と CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1。"
        "jigane が自分のプロセスに設定し、子が受け継ぐ文法の置き場のパス"
        "（TREE_SITTER_LANGUAGE_PACK_CACHE_DIR）。"
        "このパスの値は利用者名を含む絶対パスで、jigane の仮想環境の中の文法の置き場を指す",
        (
            "jigane/llm_runs/claude.py:reviewer_environment",
            "jigane/languages/grammars.py:CACHE_ENV",
            "jigane/languages/grammars.py:_prepare",
        ),
        _ALL,
        user_can_provide=False,
        user_can_provide_reason="jigane が足す",
    )
    REMOVED_ENVIRONMENT = InputSpec(
        Channel.ENVIRONMENT,
        "GitHub のトークンの変数 " + "、".join(sorted(REVIEWER_STRIPPED_ENVIRONMENT_VARIABLES)),
        ("jigane/llm_runs/claude.py:REVIEWER_STRIPPED_ENVIRONMENT_VARIABLES",),
        _ALL,
        user_can_provide=False,
        user_can_provide_reason="書き込みの資格情報を Claude Code のプロセスに存在させないため",
    )
    INHERITED_ENVIRONMENT = InputSpec(
        Channel.ENVIRONMENT,
        "除いたものと足したものを除く、親の環境のそのまま（文法の置き場のパスは含まない）。"
        "利用者の環境に置いた API キーやトークン（CLAUDE_CODE_OAUTH_TOKEN を含む）も"
        "そのまま受け継ぐ",
        ("jigane/llm_runs/claude.py:reviewer_environment",),
        _ALL,
        user_can_provide=True,
        user_can_provide_reason="普通の利用でも受け継ぐ",
    )
    WORKING_DIRECTORY = InputSpec(
        Channel.CWD,
        "cwd。機能と設計書は利用者のリポジトリの根、範囲と PR はデータの置き場の中の変更後の木。"
        "Claude Code は cwd のパスを自分の環境の情報としてモデルへ送る"
        "（Claude Code 2.1.292 で、道具を使わずにパスを答えることを確かめた）。"
        "範囲と PR ではそのパスに利用者名、JIGANE_HOME、owner/repo、"
        "レビューの識別子（@ の後に 40 桁の SHA を含む）が入る",
        (
            "jigane/skills/sharded.py:_discover_shard",
            "jigane/skills/macro.py:_outcome",
            "jigane/reviews/lifecycle.py:_build_range_content",
        ),
        _ALL,
        user_can_provide=False,
        user_can_provide_reason=(
            "範囲と PR では cwd が jigane のデータの置き場の中になり、"
            "利用者が手で claude を起動するときの cwd と違う。"
            "機能と設計書では利用者のリポジトリの根で、手でも同じ"
        ),
    )


_C = ClaudeInput
_INSTRUCTIONS = (_C.INSTRUCTIONS,)
_PRIOR = (_C.ROLE_OUTPUTS,)

# 雛形の `$名前` は、組み立てるコードが別の雛形で同じ名前を使うので、名前ごとに和を取る
PLACEHOLDER_INPUTS: Mapping[str, tuple[ClaudeInput, ...]] = {
    # macro.py:305 build_diagram_prompt
    "background": (
        _C.PAST_COMMITS,
        _C.PAST_PULL_REQUESTS,
        _C.CODEOWNERS,
        _C.DECLARATION,
        _C.INSTRUCTIONS,
    ),
    "bot_findings": (_C.BOT_COMMENTS, _C.INSTRUCTIONS),
    "declaration": (_C.DECLARATION, _C.INSTRUCTIONS),
    "scope": (_C.TARGET_FILES,),
    "target_files": (_C.TARGET_FILES,),
    # macro.py:285 diagram_materials_text と macro_design.py:16 design_materials_text
    "materials": (
        _C.TARGET_FILES,
        _C.BASE_TREE,
        _C.JUDGMENT_MAP,
        _C.SAME_SHAPE_LOCATIONS,
        _C.TECHNOLOGY_LIST,
        _C.DESIGN_DOC,
        _C.REVIEW_HISTORY,
        _C.INSTRUCTIONS,
    ),
    "standards": _INSTRUCTIONS,
    "history_rule": _INSTRUCTIONS,
    "division": _INSTRUCTIONS,
    "viewpoints": _INSTRUCTIONS,
    "discard_reason_table": _INSTRUCTIONS,
    # sharded.py:323 build_shard_prompt
    "common": _INSTRUCTIONS,
    "mode": _INSTRUCTIONS,
    "scope_rule": _INSTRUCTIONS,
    "verifier_scope_rule": _INSTRUCTIONS,
    "shard_number": _INSTRUCTIONS,
    "shard_total": _INSTRUCTIONS,
    "shard_intro": _INSTRUCTIONS,
    "shard_files": (_C.TARGET_FILES,),
    "shard_content": (_C.CHANGE_DIFF, _C.TARGET_FILES),
    "shard_checks": (_C.TARGET_FILES, _C.INSTRUCTIONS),
    "design": (_C.DESIGN_DOC, _C.INSTRUCTIONS),
    "history": (_C.REVIEW_HISTORY,),
    "macro_concerns": (_C.POLICY_CONCERNS,),
    "mcp_note": (_C.MCP_GUIDE, _C.INSTRUCTIONS),
    "mcp_guide": (_C.MCP_GUIDE,),
    # adversarial.py:200 render_prompt と :395 run_role
    "changes": (_C.CHANGE_DIFF, _C.INSTRUCTIONS),
    "candidates_json": _PRIOR,
    "challenges_json": _PRIOR,
    "alternatives_json": _PRIOR,
    "defenses_json": _PRIOR,
    "excerpts_json": (_C.SOURCE_EXCERPTS,),
    # triage.py:36 と bundle_disclosed.py:36
    "findings_json": _PRIOR,
}

# 標準入力の節の見出し（「#」〜「####」で始まる行の文）。固定の見出しは雛形に、
# 足す見出しは組み立てるコードにある。見出しの節に入る情報の種類を全て引く。
# 節は次の見出しまでで、入れ子の見出しの下の情報は入れ子の見出しの側に数える
HEADING_INPUTS: Mapping[str, tuple[ClaudeInput, ...]] = {
    # micro.md、security.md の $shard_files と $mode、macro_diagram.md の $target_files
    "## レビューの対象": (_C.TARGET_FILES, _C.INSTRUCTIONS),
    "## 受け持ち": _INSTRUCTIONS,
    "## 読み方": _INSTRUCTIONS,
    "## 出す指摘の基準": _INSTRUCTIONS,
    "## 参照の体系": _INSTRUCTIONS,
    "## 観点": _INSTRUCTIONS,
    "## 進め方": _INSTRUCTIONS,
    "## 書き方": _INSTRUCTIONS,
    "## 束ね方": _INSTRUCTIONS,
    "## 指摘（JSON）": _PRIOR,
    # macro.py の changes_prompt_text。差分に添える設定ファイルの冒頭も差分に数える
    "### 変更": (_C.CHANGE_DIFF,),
    # sharded.py の build_shard_prompt が $shard_intro（固定の文）と $shard_content を入れる
    "### 担当のシャードの中身": (_C.CHANGE_DIFF, _C.TARGET_FILES, _C.INSTRUCTIONS),
    # sharded.py の build_shard_prompt が $shard_checks（policy_regex.py の shard_checks）を入れる
    "### 決定的な検査の札": (_C.TARGET_FILES, _C.INSTRUCTIONS),
    # macro_diagram.md の $scope
    "### レビューの範囲": (_C.TARGET_FILES,),
    # macro.py の declaration_prompt_text
    "### 宣言（この変更またはファイル群が実現しようとしていること）": (_C.DECLARATION,),
    # macro_design.py の build_design_prompt
    "### 宣言（この設計書が実現しようとしていること）": (_C.DECLARATION,),
    # macro.py の background_prompt_text。PR の本文が参照する文書は background.py の
    # find_documents が PR の本文から拾ったパスで、本文の側の情報なので DECLARATION に数える
    "### 背景": (
        _C.PAST_COMMITS,
        _C.PAST_PULL_REQUESTS,
        _C.CODEOWNERS,
        _C.DECLARATION,
    ),
    # macro_diagram.md と macro_design.md の固定の導入。$materials は入れ子の見出しの側
    "### 資料": _INSTRUCTIONS,
    "### 手戻りの大きい外形の欠陥": _INSTRUCTIONS,
    # macro.py の policy_sections。changed_files.py の materials_sections が変更後の図を、
    # 変更前の木（pull_request_context/fetching.py の extract_tree）と並べて組む
    "### 変更のツリー図": (_C.TARGET_FILES, _C.BASE_TREE),
    "### クラス図": (_C.TARGET_FILES, _C.BASE_TREE),
    "### シーケンス図": (_C.TARGET_FILES, _C.BASE_TREE),
    # macro.py の bot_findings
    "### レビューボットの指摘の一覧": (_C.BOT_COMMENTS,),
    # changed_files.py の materials_sections
    "### 判断の箇所の一覧": (_C.JUDGMENT_MAP,),
    # policy_repeats.py の repeats_text
    "### 同じ形の箇所": (_C.SAME_SHAPE_LOCATIONS,),
    "#### 同じ並びが他にもある塊": (_C.SAME_SHAPE_LOCATIONS,),
    "#### 直した形が残る箇所": (_C.SAME_SHAPE_LOCATIONS,),
    # background.py の find_past_changes、find_past_pull_requests、find_owners、find_documents
    "#### 同じファイルに触れた過去のコミット": (_C.PAST_COMMITS,),
    "#### 同じファイルに触れた過去の PR": (_C.PAST_PULL_REQUESTS,),
    "#### 変更したパスの所有者（CODEOWNERS）": (_C.CODEOWNERS,),
    "#### PR の本文が参照する文書": (_C.DECLARATION,),
    # technology_list/materials.py の short_section_text
    "### 技術選定": (_C.TECHNOLOGY_LIST,),
    # history.py の history_text
    "### 前の回の指摘": (_C.REVIEW_HISTORY,),
    # design_doc.py の DesignMaterials.sections が設計書の資料の節を並べる
    "### 設計書の全文": (_C.DESIGN_DOC,),
    # 付属の文書があると、設計書の全文の見出しが「主の設計書」に替わる（design_doc.py）
    "### 主の設計書": (_C.DESIGN_DOC,),
    "#### 主の設計書": (_C.DESIGN_DOC,),
    # sharded.py の _design_text が micro と security のシャードに入れる節
    "### 設計書の資料": (_C.DESIGN_DOC,),
    "#### 受け持ちの表": _INSTRUCTIONS,
    "#### 設計書の構造": (_C.DESIGN_DOC,),
    "#### 選んだファイルと推しの理由": (_C.DESIGN_DOC,),
    "### 設計書の構造": (_C.DESIGN_DOC,),
    "### 名指しの照合の表": (_C.DESIGN_DOC,),
    "### 選んだファイルと推しの理由": (_C.DESIGN_DOC,),
    "### いまの形の図": (_C.DESIGN_DOC,),
    "#### ツリー図": (_C.DESIGN_DOC,),
    "#### クラス図": (_C.DESIGN_DOC,),
    "#### シーケンス図": (_C.DESIGN_DOC,),
}

# argv の引数の名前（値は含まない）
ARGV_INPUTS: Mapping[str, ClaudeInput] = {
    "--restricted": _C.TOOL_PERMISSIONS,
    "--settings": _C.TOOL_PERMISSIONS,
    "--tools": _C.TOOL_PERMISSIONS,
    "--strict-mcp-config": _C.TOOL_PERMISSIONS,
    "-p": _C.OUTPUT_SCHEMA,
    "--output-format": _C.OUTPUT_SCHEMA,
    "--json-schema": _C.OUTPUT_SCHEMA,
    "--model": _C.RUN_LIMITS,
    "--max-turns": _C.RUN_LIMITS,
    "--effort": _C.RUN_LIMITS,
    "--max-budget-usd": _C.RUN_LIMITS,
    "--mcp-config": _C.MCP_SERVERS,
    "--allowedTools": _C.MCP_SERVERS,
}

# 子の環境へ足された変数と、親から除かれた変数の名前
ENVIRONMENT_INPUTS: Mapping[str, ClaudeInput] = {
    **dict.fromkeys(
        (CLAUDEAI_MCP_SWITCH, NONESSENTIAL_TRAFFIC_SWITCH, CACHE_ENV), _C.ADDED_ENVIRONMENT
    ),
    **dict.fromkeys(sorted(REVIEWER_STRIPPED_ENVIRONMENT_VARIABLES), _C.REMOVED_ENVIRONMENT),
}

# 見出しにパスが入り、完全一致で引けないもの。design_doc.py の SECTION_ATTACHMENT_PREFIX
HEADING_PREFIX_INPUTS: Mapping[str, tuple[ClaudeInput, ...]] = {
    "### 付属の文書: ": (_C.DESIGN_DOC,),
    "#### 付属の文書: ": (_C.DESIGN_DOC,),
}

# 見出しではなく、固定のラベルや太字だけの行で区切る節。行の全文に一致するもの。
# 固定のラベルは雛形（skills/prompts/*.md）と policy_materials/design_doc.py にある
LABEL_INPUTS: Mapping[str, tuple[ClaudeInput, ...]] = {
    # rebuttal.md の固定の導入
    "問うこと:": _INSTRUCTIONS,
    # 先の役の出力を JSON で渡す節。adversarial.py の render_prompt が差し込む
    "候補 (JSON):": _PRIOR,
    "反証役の出力 (JSON):": _PRIOR,
    "代案役の出力 (JSON):": _PRIOR,
    "擁護役の出力 (JSON):": _PRIOR,
    # adversarial.py が作業ディレクトリから読み直した抜粋
    "反証役が確かめた位置と抜粋 (JSON):": (_C.SOURCE_EXCERPTS,),
    # design_doc.py の見出しの木の節
    "見出しの木（印は受け持ちの目安）:": (_C.DESIGN_DOC,),
}

# 値の入る行は接頭辞で引く。通し番号や名前、パスが続く
LABEL_PREFIX_INPUTS: Mapping[str, tuple[ClaudeInput, ...]] = {
    "信頼の境界: ": _INSTRUCTIONS,
    # design_doc.py の _numbered_text
    "設計書のパス: ": (_C.DESIGN_DOC,),
    "付属の文書のパス: ": (_C.DESIGN_DOC,),
    # sharded.py が MCP を使える群の標準入力へ足す
    "次の MCP が使える: ": (_C.MCP_GUIDE, _C.INSTRUCTIONS),
    # sequence/review_diagrams.py の NO_CALL_ENTRIES_NOTE と UNRELATED_CALL_ENTRIES_NOTE
    "呼び出しの無い入口 ": (_C.TARGET_FILES, _C.BASE_TREE),
    "変更に関わる呼び出しの無い入口 ": (_C.TARGET_FILES, _C.BASE_TREE),
}
