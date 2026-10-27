"""SECURITY.md の「レビューの入力と Claude Code」に、Claude Code へ渡す情報の表を作る。

    uv run --locked python scripts/generate_claude_inputs_table.py [--check]

表は jigane.llm_runs.claude_inputs の ClaudeInput の項目から作り、SECURITY.md の開始と終了の
印の間に書く。印の外は一文字も変えない。--check は書かずに、印の間が最新かだけを見る。
"""

import argparse
import sys
from pathlib import Path

from generated_regions import check_or_write, fail, read_exact, replace_region

from jigane.groups.definitions import GroupId
from jigane.llm_runs.claude_inputs import Channel, ClaudeInput, GroupRole

ROOT = Path(__file__).resolve().parent.parent

SCRIPT = "scripts/generate_claude_inputs_table.py"
BEGIN = f"<!-- BEGIN GENERATED: claude-inputs-table ({SCRIPT}) -->"
END = "<!-- END GENERATED: claude-inputs-table -->"

CHANNEL_NAMES: dict[Channel, str] = {
    Channel.PROMPT: "標準入力",
    Channel.ARGV: "コマンドの引数",
    Channel.ENVIRONMENT: "環境変数",
    Channel.CWD: "作業ディレクトリ",
}

# 渡さないものの項目。これだけを表 2 へ回し、表 1 からは除く
NOT_PASSED = ClaudeInput.REMOVED_ENVIRONMENT

OUTSIDE_SENTENCE = (
    "Claude Code が自分で足してモデルへ送る情報（Claude Code の版や設定による）は、"
    "この表の外です。"
)
GROUPS_SENTENCE = "「渡る群」の macro は方針、micro は実装、security はセキュリティの群です。"
# 道具の名前は claude.py の build_command と security.py の DISCOVERY_TOOLS が渡すもの
ENVIRONMENT_SENTENCE = (
    "環境変数は Claude Code のプロセスに置かれるだけです。"
    "jigane が渡す道具は Read、Grep、Glob と、"
    "MCP の資源を読む ReadMcpResourceTool、ListMcpResourcesTool だけで、"
    "--restricted で作業ディレクトリの中に閉じるので、"
    "モデルが環境変数を読んで送る道具は jigane からは渡りません。"
    "ただし --mcps で渡した MCP サーバは、Claude Code が起動するプロセスで、"
    "環境変数を受け継ぐことがあります。"
    "その道具が何を読んでモデルへ返すかは、サーバによります。"
)


def cell(text: str) -> str:
    """表のセルに入れる文。縦線は escape し、改行は空白にする。"""
    return text.replace("|", "\\|").replace("\n", " ")


def roles_text(roles: tuple[GroupRole, ...]) -> str:
    """渡る群の名前を GroupId の順に並べる。役の名前は利用者に意味が無いので載せない。"""
    present = {group_role.group for group_role in roles}
    return "、".join(group.value for group in GroupId if group in present)


def render_table() -> str:
    """印を含む表の全文を返す。"""
    lines = [
        BEGIN,
        "",
        OUTSIDE_SENTENCE,
        "",
        GROUPS_SENTENCE,
        "",
        "#### 渡すもの",
        "",
        "| 経路 | 中身 | 渡る群 | 利用者も手で渡せるか |",
        "| --- | --- | --- | --- |",
    ]
    for item in ClaudeInput:
        if item is NOT_PASSED:
            continue
        spec = item.value
        can_provide = "はい" if spec.user_can_provide else "いいえ"
        lines.append(
            f"| {CHANNEL_NAMES[spec.channel]} | {cell(spec.content)} "
            f"| {cell(roles_text(spec.roles))} "
            f"| {can_provide}（{cell(spec.user_can_provide_reason)}） |"
        )
    removed = NOT_PASSED.value
    lines += [
        "",
        ENVIRONMENT_SENTENCE,
        "",
        "#### 渡さないもの",
        "",
        "| 経路 | 中身 | 理由 |",
        "| --- | --- | --- |",
        (
            f"| {CHANNEL_NAMES[removed.channel]} | {cell(removed.content)} "
            f"| {cell(removed.user_can_provide_reason)} |"
        ),
        "",
        END,
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SECURITY.md の Claude Code への入力の表を作る")
    parser.add_argument("--root", type=Path, default=ROOT, help="SECURITY.md の置き場")
    parser.add_argument("--check", action="store_true", help="書かずに、最新かだけを見る")
    arguments = parser.parse_args(argv)
    path = arguments.root / "SECURITY.md"
    try:
        expected = replace_region(read_exact(path), BEGIN, END, render_table(), "SECURITY.md")
    except ValueError as error:
        return fail(error)
    return check_or_write(
        path,
        expected,
        check=arguments.check,
        guidance="`make generate`（Windows は `just generate`）で作り直す",
    )


if __name__ == "__main__":
    sys.exit(main())
