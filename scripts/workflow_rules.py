"""ワークフローの読み取りと違反の判定。公開の写しを作る道具と、ワークフローの検査が共有する。

YAML は ruamel.yaml で構文木に読んでから判定する。行ごとの正規表現とインデントの比較では、引用や
フロー形式や文字列の中身を取り違えてすり抜けるため。危険なトリガ、permissions の欠落と
write-all、unpinned-uses は zizmor（check_workflows.py）が落とすので、ここでは持たない。
cache と persist-credentials、許可一覧との照合、除外一覧の self-hosted は zizmor が見ないので持つ。
run の中の式の直展開は zizmor が見落とす形（引用符の中の # のあと）があるので持つ。

scripts の中に置き、本体の jigane からは import しない。
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

EXCLUDE_LIST = Path(".github/public-exclude.txt")

# 公開リポジトリで使ってよい secrets の名前。GITHUB_TOKEN も含め、必要になったらここへ足す。
ALLOWED_SECRETS: frozenset[str] = frozenset()

# GitHub がホストするランナーの名前。式や self-hosted のラベルをここに通さない。
HOSTED_RUNNER = re.compile(r"(ubuntu|macos|windows)-(latest|\d+(\.\d+)*)")
SELF_HOSTED = re.compile(r"self-hosted", re.IGNORECASE)
EVENT_NAME = re.compile(r"[A-Za-z_][\w-]*")

# 以下は構文木から取り出した 1 つの文字列の中身を見る。YAML の行を見るのではない。
# 添字の形（github['event']）と、他の式と混ざった形も拾う。
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
# github と secrets への参照。ドットの形と添字の形を別々の正規表現で拾い、複雑さを抑える。
DOT_ACCESS = re.compile(r"\b(github|secrets)\s*\.\s*(\w+)")
SUBSCRIPT_ACCESS = re.compile(r"\b(github|secrets)\s*\[\s*['\"](\w+)['\"]\s*\]")
SECRET_WORD = re.compile(r"\bsecrets\b")
# 名前を静的に決められない参照（toJSON(secrets)、secrets[matrix.x] など）の表示
UNRESOLVED_REFERENCE = "(名前を決められない参照)"
CACHE_ACTION = re.compile(r"actions/cache(?:[@/]|$)", re.IGNORECASE)
CHECKOUT_ACTION = re.compile(r"actions/checkout(?:@|$)", re.IGNORECASE)
CACHE_OPTIONS = ("cache", "enable-cache")

YamlMap = dict[object, object]
YamlSeq = list[object]


@dataclass(frozen=True)
class Readable:
    """構文木に読めたワークフロー。"""

    text: str
    tree: YamlMap


@dataclass(frozen=True)
class Unreadable:
    """読めない、または最上位が対応表でないワークフロー。本文だけが残る。"""

    text: str


Workflow = Readable | Unreadable


class Kind(Enum):
    RUNNER_NOT_HOSTED = "runs-on が GitHub のホストするランナーの名前そのものではない"
    SELF_HOSTED_TEXT = "self-hosted の文字列"
    EVENT_EXPANSION = "run への github.event の直展開"
    HEAD_REF_EXPANSION = "run への github.head_ref の直展開"
    CACHE_ACTION = "actions/cache の使用"
    CHECKOUT_CREDENTIALS = "actions/checkout に persist-credentials: false が無い"
    DISALLOWED_REFERENCE = "許可されていない secrets"
    CACHE_OPTION = "キャッシュの有効化"
    NOT_PUSH_OR_SCHEDULE = "self-hosted を使うのに、push と schedule 以外でも起動する"
    ON_UNREADABLE = "on: を読めない"
    UNREADABLE = "YAML として読めない"


# 公開の写しに入るワークフローでは許さず、除外の一覧のワークフローでは許す種類。
RUNNER_KINDS = frozenset({Kind.RUNNER_NOT_HOSTED, Kind.SELF_HOSTED_TEXT})
# 公開の写しの検査（export_public）が落とす種類。読めないワークフローは、ランナーを確かめられない。
PUBLIC_COPY_KINDS = RUNNER_KINDS | {Kind.UNREADABLE}


@dataclass(frozen=True)
class Violation:
    kind: Kind
    detail: str = ""
    line: int | None = None

    def __str__(self) -> str:
        return f"{self.kind.value}: {self.detail}" if self.detail else self.kind.value


def read_entries(path: Path) -> list[str]:
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def load_workflow(text: str) -> Workflow:
    """ワークフローを構文木に読む。読めたかの判別はここだけで行う。

    ruamel.yaml は YAML 1.2 で読むため、キー on は真偽値にならず文字列のまま残る。
    """
    try:
        document = YAML(typ="rt").load(text)
    except YAMLError:
        return Unreadable(text)
    return Readable(text, document) if isinstance(document, dict) else Unreadable(text)


def as_map(value: object) -> YamlMap:
    return value if isinstance(value, dict) else {}


def as_seq(value: object) -> YamlSeq:
    return value if isinstance(value, list) else []


def scalar_text(value: object) -> str:
    """真偽値は YAML の書き方（true / false）に戻す。"""
    return str(value).lower() if isinstance(value, bool) else str(value)


def jobs_of(workflow: YamlMap) -> Iterator[YamlMap]:
    for job in as_map(workflow.get("jobs")).values():
        yield as_map(job)


def steps_of(workflow: YamlMap) -> Iterator[YamlMap]:
    for job in jobs_of(workflow):
        for step in as_seq(job.get("steps")):
            yield as_map(step)


def strings_in(node: object) -> Iterator[str]:
    """構文木の中の文字列の値を全部返す。キーとコメントは含まない。"""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for child in node.values():
            yield from strings_in(child)
    elif isinstance(node, list):
        for child in node:
            yield from strings_in(child)


def expressions_in(text: str) -> Iterator[str]:
    for match in EXPRESSION.finditer(text):
        yield match.group(1)


def accessed_members(expression: str, root: str) -> list[str]:
    """式の中で root.名前 または root['名前'] と参照されている名前を、現れた順に返す。"""
    matches = [*DOT_ACCESS.finditer(expression), *SUBSCRIPT_ACCESS.finditer(expression)]
    return [m.group(2) for m in sorted(matches, key=lambda m: m.start()) if m.group(1) == root]


def runs_on_labels(value: object) -> list[str]:
    """runs-on の値から、ラベルを取り出す。group: などの対応表は「キー: 値」の文字列にして残す。"""
    if isinstance(value, dict):
        return [f"{key}: {scalar_text(item)}" for key, item in value.items()]
    if isinstance(value, list):
        return [scalar_text(item) for item in value]
    return [] if value is None else [scalar_text(value)]


def runner_violations(workflow: Workflow) -> list[Violation]:
    """GitHub のホストするランナー以外を指す runs-on と、self-hosted の文字列。"""
    found: list[Violation] = []
    jobs = as_map(workflow.tree.get("jobs")) if isinstance(workflow, Readable) else {}
    for job in jobs.values():
        if not isinstance(job, dict) or "runs-on" not in job:
            # uses: で呼ぶ reusable workflow の job には runs-on が無い。
            # ランナーは呼ばれる側が決める
            continue
        line = job.lc.value("runs-on")[0] + 1 if isinstance(job, CommentedMap) else None
        found.extend(
            Violation(Kind.RUNNER_NOT_HOSTED, label or "(空)", line)
            for label in runs_on_labels(job["runs-on"]) or [""]
            if not HOSTED_RUNNER.fullmatch(label)
        )
    # コメントの下書きは戻されうるため、構文木ではなくコメントも含めた全文から探す。
    found.extend(
        Violation(Kind.SELF_HOSTED_TEXT, line=number)
        for number, line in enumerate(workflow.text.splitlines(), start=1)
        if SELF_HOSTED.search(line)
    )
    return found


def event_names(names: set[str]) -> set[str] | None:
    return names if all(EVENT_NAME.fullmatch(name) for name in names) else None


def trigger_events(workflow: Workflow) -> set[str] | None:
    """最上位の on: が起こす出来事の名前。on: が無ければ空、読めない形なら None。"""
    if not isinstance(workflow, Readable):
        return None
    tree = workflow.tree
    if "on" not in tree:
        # on: が無いワークフローは何の出来事でも起動しない。読めないのとは別に扱う。
        return set()
    trigger = tree["on"]
    # 文字列ひとつ、列、対応表（キーが出来事の名前）のどれかで書ける。
    # それ以外（空の on: など）は読めない
    names = (
        [trigger]
        if isinstance(trigger, str)
        else list(trigger)
        if isinstance(trigger, list | dict)
        else None
    )
    if names is None or not all(isinstance(name, str) for name in names):
        return None
    return event_names({name for name in names if isinstance(name, str)})


def step_violations(step: YamlMap) -> list[Violation]:
    found: list[Violation] = []
    run = step.get("run")
    if isinstance(run, str):
        expanded = list(expressions_in(run))
        github_members = {m for e in expanded for m in accessed_members(e, "github")}
        if "event" in github_members:
            found.append(Violation(Kind.EVENT_EXPANSION))
        if "head_ref" in github_members:
            found.append(Violation(Kind.HEAD_REF_EXPANSION))
    uses = step.get("uses")
    options = as_map(step.get("with"))
    if isinstance(uses, str) and CACHE_ACTION.match(uses):
        found.append(Violation(Kind.CACHE_ACTION))
    if (
        isinstance(uses, str)
        and CHECKOUT_ACTION.match(uses)
        and scalar_text(options.get("persist-credentials")) != "false"
    ):
        found.append(Violation(Kind.CHECKOUT_CREDENTIALS))
    found.extend(
        Violation(Kind.CACHE_OPTION, f"{name}: {scalar_text(options[name])}")
        for name in CACHE_OPTIONS
        if name in options and scalar_text(options[name]) != "false"
    )
    return found


def secret_violations(tree: YamlMap) -> list[Violation]:
    found: list[Violation] = []
    for text in strings_in(tree):
        for expression in expressions_in(text):
            named = accessed_members(expression, "secrets")
            found.extend(
                Violation(Kind.DISALLOWED_REFERENCE, name)
                for name in named
                if name not in ALLOWED_SECRETS
            )
            if len(SECRET_WORD.findall(expression)) > len(named):
                found.append(Violation(Kind.DISALLOWED_REFERENCE, UNRESOLVED_REFERENCE))
    return found


def workflow_violations(workflow: Workflow) -> list[Violation]:
    """ワークフロー 1 つの違反を、runs-on の違反も含めて全部返す。"""
    found = runner_violations(workflow)
    if not isinstance(workflow, Readable):
        return [*found, Violation(Kind.UNREADABLE)]
    for step in steps_of(workflow.tree):
        found += step_violations(step)
    return found + secret_violations(workflow.tree)


def violations(text: str) -> list[Violation]:
    """本文を一度だけ読んで違反を返す、外から呼ぶ入口。"""
    return workflow_violations(load_workflow(text))


def exempt_violations(relative: str, text: str, excluded: frozenset[str]) -> list[Violation]:
    """除外の一覧に載るファイルだけ self-hosted を許し、起動を push か push と schedule に縛る。

    schedule は既定の枝のコードで走り PR に由来しない。PR 由来のコードが走る経路は増えない。
    """
    workflow = load_workflow(text)
    found = workflow_violations(workflow)
    if relative not in excluded:
        return found
    uses_self_hosted = any(v.kind is Kind.SELF_HOSTED_TEXT for v in found)
    found = [v for v in found if v.kind not in RUNNER_KINDS]
    if not uses_self_hosted:
        # self-hosted を使わない除外のワークフローは、PR 由来のコードがホストで走る経路を持たない
        return found
    events = trigger_events(workflow)
    if events is None:
        found.append(Violation(Kind.ON_UNREADABLE))
    elif events not in ({"push"}, {"push", "schedule"}):
        found.append(Violation(Kind.NOT_PUSH_OR_SCHEDULE, str(sorted(events))))
    return found
