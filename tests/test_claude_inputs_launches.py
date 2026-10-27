"""claude の起動の記録が、claude_inputs の対応表と ClaudeInput の roles に載ることを確かめる。"""

import re
from collections.abc import Iterator
from functools import cache
from importlib import resources
from pathlib import Path
from string import Template

import pytest
from claude_inputs_support import WRAPPER_ENVIRONMENT, Launch, recorded_launches

from jigane.groups.definitions import GroupId
from jigane.languages.grammars import CACHE_ENV, cache_root
from jigane.llm_runs.claude_inputs import (
    ARGV_INPUTS,
    ENVIRONMENT_INPUTS,
    HEADING_INPUTS,
    HEADING_PREFIX_INPUTS,
    LABEL_INPUTS,
    LABEL_PREFIX_INPUTS,
    ClaudeInput,
    GroupRole,
    Role,
)
from jigane.skills.macro import NO_BOT_FINDINGS_TEXT
from jigane.storage.store import ReviewKind

HEADING = re.compile(r"#{1,4} ")
ARGUMENT_NAME = re.compile(r"-{1,2}[A-Za-z]")
# 見出しでない節の区切り。太字だけの行と、日本語で始まる「ラベル:」の行
BOLD_ONLY = re.compile(r"\*\*[^*].*\*\*")
LABEL_LINE = re.compile(r"[^\x00-\x7f][^:：]{0,39}[:：](?: |$)")
FENCE = "```"
# 雛形はあるが、記録に現れない役。方針の群から検証を外した決定の結果、代案の形の経路
# （skills/adversarial.py の split_candidates）が残った。
# 開発の決定 alternative-roles-unreachable-recorded。
# 起動するようになったら、この一覧を直す必要に気づくために、現れたら落とす
UNREACHABLE_ROLES = frozenset({Role.ALTERNATIVE, Role.DEFENSE, Role.JUDGE_ALTERNATIVE})
_FOLLOWERS = {
    GroupId.MICRO: (
        Role.MICRO,
        Role.REBUTTAL,
        Role.JUDGE_REBUTTAL,
        Role.BUNDLE_DISCLOSED,
        Role.THEMES,
    ),
    GroupId.SECURITY: (
        Role.SECURITY,
        Role.REBUTTAL,
        Role.JUDGE_REBUTTAL,
        Role.BUNDLE_DISCLOSED,
    ),
}
LAUNCHED_ROLES = {
    (kind, group, role)
    for kind in ReviewKind
    for group, roles in (
        (
            GroupId.MACRO,
            (Role.MACRO_DESIGN if kind is ReviewKind.DESIGN_DOC else Role.MACRO_DIAGRAM,),
        ),
        *_FOLLOWERS.items(),
    )
    for role in roles
}
# 背景の節は 4 種類の項目の入れ物で、どれが入ったかは中の小見出しに現れる
CONTAINER_HEADINGS = frozenset({"### 背景"})
# 雛形が常に出す見出しで、渡すものが無いときは固定の文が入る。固定の文なら項目は渡っていない
EMPTY_TEXTS = {"### レビューボットの指摘の一覧": NO_BOT_FINDINGS_TEXT}
# PR の本文が参照する文書の見出し
PULL_REQUEST_DOCUMENT_HEADING = "#### PR の本文が参照する文書"


# 表にあるが今の作り物では出せない鍵と、出せない理由。記録に現れたら、表の鍵の確かめが落ちる
UNREACHABLE_KEYS: dict[str, dict[str, str]] = {
    "label": {
        "代案役の出力 (JSON):": "代案役は起動の記録に現れない役（UNREACHABLE_ROLES）",
        "擁護役の出力 (JSON):": "擁護役は起動の記録に現れない役（UNREACHABLE_ROLES）",
    },
    "label_prefix": {
        "変更に関わる呼び出しの無い入口 ": "変更に関わらない入口を持つファイルが無く出ない。"
        "出す作り物を足せたら消す",
    },
}


@pytest.fixture(scope="module")
def launches(tmp_path_factory: pytest.TempPathFactory) -> Iterator[list[Launch]]:
    with recorded_launches(tmp_path_factory.mktemp("claude_inputs")) as recorded:
        yield recorded


def _headings(launch: Launch) -> list[str]:
    found: list[str] = []
    fenced = False
    for line in str(launch["stdin"]).splitlines():
        if line.startswith(FENCE):
            fenced = not fenced
        elif not fenced and HEADING.match(line):
            found.append(line.strip())
    return found


@cache
def _prompt_fragments(name: str) -> tuple[str, ...]:
    """雛形 prompts/<name>.md を差し込み口（$xxx、${xxx}）で区切った、固定の文の断片。"""
    text = resources.files("jigane.skills").joinpath(f"prompts/{name}.md").read_text("utf-8")
    pieces: list[str] = []
    position = 0
    for match in Template.pattern.finditer(text):
        if match.group("escaped") is not None:
            continue
        pieces.append(text[position : match.start()])
        position = match.end()
    pieces.append(text[position:])
    return tuple(piece.strip() for piece in pieces if piece.strip())


@cache
def _inserted_part_names() -> tuple[str, ...]:
    """役の雛形でなく、役の雛形へ差し込まれる部品（common、scope_rule など）の名前。"""
    roles = {role.value for role in Role}
    directory = resources.files("jigane.skills").joinpath("prompts")
    names = (path.name.removesuffix(".md") for path in directory.iterdir())
    return tuple(sorted(n for n in names if n not in roles))


def _kept_markers(fragment: str) -> str:
    """断片のうち、表に載る見出しとラベルの行。取り除いた跡に、節の始まりとして残す。"""
    return "\n".join(
        line
        for line in fragment.splitlines()
        if (HEADING.match(line) and _heading_inputs(line.strip()) is not None)
        or (_is_label_line(line) and _label_inputs(line) is not None)
    )


def _without_template_text(launch: Launch) -> str:
    """標準入力から雛形の固定の文を取り除く。表に載る見出しとラベルの行は節の始まりとして残す。

    役の雛形の断片は順に（直前の位置より後ろから）探し、差し込まれる部品の断片はどこからでも探す。
    """
    text = str(launch["stdin"])
    position = 0
    for fragment in _prompt_fragments(launch.role):
        index = text.find(fragment, position)
        assert index >= 0, f"{_label(launch)}: 雛形の断片が標準入力に無い: {fragment[:80]!r}"
        marker = f"\n{_kept_markers(fragment)}\n"
        text = text[:index] + marker + text[index + len(fragment) :]
        position = index + len(marker)
    for part in _inserted_part_names():
        for fragment in _prompt_fragments(part):
            text = text.replace(fragment, f"\n{_kept_markers(fragment)}\n")
    return text


def _filled_headings(launch: Launch) -> list[str]:
    """節（次の見出しまで）に、雛形の固定の文でない空白でない行がある見出し。

    本文の無い見出し、固定の文だけの見出しでは項目は渡らない。
    """
    filled: list[str] = []
    current: str | None = None
    fenced = False
    for line in _without_template_text(launch).splitlines():
        if line.startswith(FENCE):
            fenced = not fenced
        elif not fenced and HEADING.match(line):
            current = line.strip()
            continue
        if current is not None and line.strip() and current not in filled:
            filled.append(current)
    return filled


def _is_label_line(line: str) -> bool:
    return bool(BOLD_ONLY.fullmatch(line) or LABEL_LINE.match(line))


def _label_lines(launch: Launch) -> list[str]:
    """見出しでなく、太字だけか、ラベルの形の行。コードの囲みの中は数えない。"""
    found: list[str] = []
    fenced = False
    for line in str(launch["stdin"]).splitlines():
        if line.startswith(FENCE):
            fenced = not fenced
        elif not fenced and not HEADING.match(line) and _is_label_line(line):
            found.append(line)
    return found


def _label_inputs(line: str) -> tuple[ClaudeInput, ...] | None:
    """ラベルの行の項目。全文の一致に無ければ、値の入る行の接頭辞で引く。"""
    if line in LABEL_INPUTS:
        return LABEL_INPUTS[line]
    for prefix, items in LABEL_PREFIX_INPUTS.items():
        if line.startswith(prefix):
            return items
    return None


def _heading_inputs(heading: str) -> tuple[ClaudeInput, ...] | None:
    """見出しの項目。完全一致に無ければ、パスの入る見出しの接頭辞で引く。"""
    if heading in HEADING_INPUTS:
        return HEADING_INPUTS[heading]
    for prefix, items in HEADING_PREFIX_INPUTS.items():
        if heading.startswith(prefix):
            return items
    return None


def _received(launches: list[Launch], kind: ReviewKind) -> set[ClaudeInput]:
    """そのレビューの種類の起動が、見出しで受けた項目の集合。"""
    return {
        item
        for launch in launches
        if launch.kind is kind
        for heading in _filled_headings(launch)
        if heading not in CONTAINER_HEADINGS
        and not (
            heading in EMPTY_TEXTS
            and f"{heading}\n\n{EMPTY_TEXTS[heading]}" in str(launch["stdin"])
        )
        for item in _heading_inputs(heading) or ()
    }


def _argument_names(launch: Launch) -> list[str]:
    # 記録の argv は claude の後に続く引数だけで、先頭も jigane の渡した引数
    return [token for token in launch["argv"] if ARGUMENT_NAME.match(token)]


def _label(launch: Launch) -> str:
    return f"{launch.kind}/{launch.group}/{launch.role}"


def _missing_roles(launch: Launch, inputs: set[ClaudeInput]) -> list[str]:
    key = GroupRole(launch.group, Role(launch.role))
    return [item.name for item in inputs if key not in item.value.roles]


def test_launched_roles_are_the_expected_ones_and_none_is_unreachable(
    launches: list[Launch],
) -> None:
    roles = {(launch.kind, launch.group, Role(launch.role)) for launch in launches}
    assert not {role for _, _, role in roles} & UNREACHABLE_ROLES
    assert roles == LAUNCHED_ROLES


def test_every_heading_of_the_prompt_is_in_heading_inputs(launches: list[Launch]) -> None:
    unknown = {(_label(launch), h) for launch in launches for h in _headings(launch)}
    assert {pair for pair in unknown if _heading_inputs(pair[1]) is None} == set()


def test_every_argument_name_is_in_argv_inputs(launches: list[Launch]) -> None:
    unknown = {(_label(launch), n) for launch in launches for n in _argument_names(launch)}
    assert {pair for pair in unknown if pair[1] not in ARGV_INPUTS} == set()


def test_every_environment_difference_is_in_environment_inputs(launches: list[Launch]) -> None:
    for launch in launches:
        environment = launch["environment"]
        added = set(environment["added"]) - WRAPPER_ENVIRONMENT
        removed = set(environment["removed"]) - WRAPPER_ENVIRONMENT
        for name in added:
            assert ENVIRONMENT_INPUTS.get(name) is ClaudeInput.ADDED_ENVIRONMENT, name
        for name in removed:
            assert ENVIRONMENT_INPUTS.get(name) is ClaudeInput.REMOVED_ENVIRONMENT, name
        # 親に資格情報を置いたので、除く変数は全て除かれている
        expected_removed = {
            name
            for name, item in ENVIRONMENT_INPUTS.items()
            if item is ClaudeInput.REMOVED_ENVIRONMENT
        }
        assert removed == expected_removed, _label(launch)
        # 文法の置き場の変数は、値に利用者のホームのパスが入る。jigane の置き場を指すことを確かめる
        assert Path(environment["added"][CACHE_ENV]).is_relative_to(cache_root()), _label(launch)


def test_every_launched_role_is_in_the_roles_of_the_inputs_it_received(
    launches: list[Launch],
) -> None:
    for launch in launches:
        inputs = {
            *(item for h in _filled_headings(launch) for item in _heading_inputs(h) or ()),
            *(ARGV_INPUTS[n] for n in _argument_names(launch) if n in ARGV_INPUTS),
            *(
                ENVIRONMENT_INPUTS[n]
                for n in (*launch["environment"]["added"], *launch["environment"]["removed"])
                if n in ENVIRONMENT_INPUTS
            ),
        }
        assert _missing_roles(launch, inputs) == [], _label(launch)


def test_pull_requests_receive_bot_comments_past_pull_requests_owners_and_documents(
    launches: list[Launch],
) -> None:
    # 他の種類で渡らないことは確かめない。作り物の材料の偶然を、製品の規則として固定しないため
    received = _received(launches, ReviewKind.PULL_REQUEST)
    assert {
        ClaudeInput.BOT_COMMENTS,
        ClaudeInput.PAST_PULL_REQUESTS,
        ClaudeInput.CODEOWNERS,
    } <= received
    # DECLARATION は他の種類にも宣言の見出しで入るので、PR の文書の見出しで確かめる
    assert any(
        PULL_REQUEST_DOCUMENT_HEADING in _headings(launch)
        for launch in launches
        if launch.kind is ReviewKind.PULL_REQUEST
    )


def test_ranges_receive_past_commits_and_run_under_the_data_home(
    launches: list[Launch],
) -> None:
    assert ClaudeInput.PAST_COMMITS in _received(launches, ReviewKind.RANGE)
    for launch in launches:
        if launch.kind is ReviewKind.RANGE:
            assert Path(launch["cwd"]).is_relative_to(launch["home"]), _label(launch)
        elif launch.kind is ReviewKind.FEATURE:
            assert not Path(launch["cwd"]).is_relative_to(launch["home"]), _label(launch)


def test_design_doc_reviews_launch_macro_design_and_give_the_document_to_micro_and_security(
    launches: list[Launch],
) -> None:
    design = [launch for launch in launches if launch.kind is ReviewKind.DESIGN_DOC]
    assert any(launch.role == Role.MACRO_DESIGN for launch in design)
    for group in (GroupId.MICRO, GroupId.SECURITY):
        discoverers = [x for x in design if x.group is group and x.role == group.value]
        assert discoverers, group
        for launch in discoverers:
            inputs = {i for h in _headings(launch) for i in _heading_inputs(h) or ()}
            assert ClaudeInput.DESIGN_DOC in inputs, _label(launch)
    # sharded.py の _design_insert は DiffShard と FullTextShard に空の文を返し、
    # macro_design は設計書の計画だけで起動するので、設計書の外では渡らない
    for kind in set(ReviewKind) - {ReviewKind.DESIGN_DOC}:
        assert ClaudeInput.DESIGN_DOC not in _received(launches, kind), kind


def test_every_label_line_of_the_prompt_is_in_label_inputs_and_its_role_is_in_roles(
    launches: list[Launch],
) -> None:
    unknown = {
        (_label(launch), line[:60])
        for launch in launches
        for line in _label_lines(launch)
        if _label_inputs(line) is None
    }
    assert unknown == set()
    for launch in launches:
        inputs = {i for line in _label_lines(launch) for i in _label_inputs(line) or ()}
        assert _missing_roles(launch, inputs) == [], _label(launch)


def _observed_keys(launches: list[Launch]) -> dict[str, set[str]]:
    """起動の記録に現れた鍵を、表ごとに集める。"""
    observed: dict[str, set[str]] = {
        name: set()
        for name in ("heading", "heading_prefix", "argv", "environment", "label", "label_prefix")
    }
    for launch in launches:
        for heading in _headings(launch):
            observed["heading"].add(heading)
            observed["heading_prefix"] |= {
                p for p in HEADING_PREFIX_INPUTS if heading.startswith(p)
            }
        observed["argv"] |= set(_argument_names(launch))
        environment = launch["environment"]
        observed["environment"] |= {
            *environment["added"],
            *environment["removed"],
        } - WRAPPER_ENVIRONMENT
        for line in _label_lines(launch):
            observed["label"].add(line)
            observed["label_prefix"] |= {p for p in LABEL_PREFIX_INPUTS if line.startswith(p)}
    return observed


def test_every_key_of_the_tables_appears_in_a_launch_or_is_declared_unreachable(
    launches: list[Launch],
) -> None:
    observed = _observed_keys(launches)
    tables = {
        "heading": HEADING_INPUTS,
        "heading_prefix": HEADING_PREFIX_INPUTS,
        "argv": ARGV_INPUTS,
        "environment": ENVIRONMENT_INPUTS,
        "label": LABEL_INPUTS,
        "label_prefix": LABEL_PREFIX_INPUTS,
    }
    for name, table in tables.items():
        unreachable = UNREACHABLE_KEYS.get(name, {})
        stale = set(table) - observed[name] - set(unreachable)
        assert stale == set(), f"{name}: 記録に現れない鍵（古い鍵か、出せない鍵の宣言漏れ）"
        # 出せない鍵が出せるようになったら、この宣言を消す必要に気づくために落とす
        assert set(unreachable) & observed[name] == set(), f"{name}: 出せないはずの鍵が現れた"
        assert set(unreachable) <= set(table), f"{name}: 宣言した鍵が表に無い"


def _orphan_lines(launch: Launch) -> list[str]:
    """雛形の固定の文を除いた残りのうち、表に載る見出しやラベルで始まる節の外にある行。"""
    orphans: list[str] = []
    in_section = False
    fenced = False
    for line in _without_template_text(launch).splitlines():
        if line.startswith(FENCE):
            fenced = not fenced
        if not fenced and (
            (HEADING.match(line) and _heading_inputs(line.strip()) is not None)
            or (_is_label_line(line) and _label_inputs(line) is not None)
        ):
            in_section = True
        elif not in_section and line.strip():
            orphans.append(line.strip())
    return orphans


def test_no_sentence_of_the_prompt_stands_outside_a_section_of_the_tables(
    launches: list[Launch],
) -> None:
    # 雛形の固定の文を除いた残りは差し込まれた素材だけで、全て表に載る見出しかラベルの節に入る。
    # 組み立てのコードが足す固定の文は、全て見出しかラベルの下にあり、節の外に残るものは無い。
    # 確かめていない範囲: 節の中に見出しなしで差し込まれた文は、その節の素材と区別できない
    orphans = {
        (_label(launch), line[:80]) for launch in launches for line in _orphan_lines(launch)
    }
    assert orphans == set()


def test_a_sentence_without_heading_before_the_first_section_is_found(
    launches: list[Launch],
) -> None:
    # 検査が空振りでないことの確かめ。標準入力の冒頭の文の直後に、見出しの無い文を差し込む
    launch = next(x for x in launches if x.role == Role.MICRO)
    stdin = str(launch["stdin"])
    first_line = stdin.splitlines()[0]
    inserted = f"{first_line}\n見出しの無い差し込みの文。"
    tampered = Launch(launch, stdin=stdin.replace(first_line, inserted, 1))
    assert _orphan_lines(tampered) == ["見出しの無い差し込みの文。"]
    assert _orphan_lines(launch) == []
