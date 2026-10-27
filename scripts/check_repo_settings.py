"""GitHub のリポジトリの設定を、期待の一覧（.github/repository-settings.json）と照らして差を出す。

ゲートには入れない。公開の前後など、手で走らせる。

  check_repo_settings.py --repo owner/name --profile working   作業用の private のリポジトリと
                                                               照らす
  check_repo_settings.py --repo owner/name --profile public    公開のときの期待と照らす

期待と違う項目、読めなかった項目を「パス: 期待 X、実際 Y」で出して終了コード 1。
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from jigane.git.process import CommandError, run_command

SETTINGS_PATH = Path(__file__).resolve().parent.parent / ".github" / "repository-settings.json"

ReadApi = Callable[[str], object]


TAG_RULES_SUFFIX = "/rules/tags"
TAG_SCOPE_SUFFIX = "/rulesets/tag-scope"
ARRAY_MARK = "[]."
ALERTS_SUFFIX = "/vulnerability-alerts"
RULESET_KEYS = ("id", "target", "enforcement")


class ResponseShapeError(ValueError):
    """応答が、読む側の当てにする形をしていない。"""


def read_gh(endpoint: str) -> object:
    output = run_command(["gh", "api", endpoint])
    if not output.strip() and endpoint.endswith(ALERTS_SUFFIX):
        # vulnerability-alerts だけは、有効のとき 204 で本文が空になる。
        # 404 は CommandError で別に出る。
        # 他の読み先の空の本文は有効の印ではないので、下の json.loads で読めない扱いにする
        return {"enabled": True}
    return json.loads(output)


def read_gh_pages(endpoint: str) -> object:
    """全ページを読み、ページごとの配列を並べた配列で返す。"""
    return json.loads(run_command(["gh", "api", "--paginate", "--slurp", endpoint]))


def list_rulesets(repo: str, read_pages: ReadApi) -> list[dict[str, object]]:
    pages = read_pages(f"repos/{repo}/rulesets")
    if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
        raise ResponseShapeError("応答の形が違う: ルールセットの一覧がページごとの配列でない")
    rulesets: list[dict[str, object]] = []
    for page in pages:
        for ruleset in page:
            if not isinstance(ruleset, dict) or not all(key in ruleset for key in RULESET_KEYS):
                raise ResponseShapeError(
                    f"応答の形が違う: ルールセットが {'・'.join(RULESET_KEYS)} を持つ dict でない"
                )
            rulesets.append(ruleset)
    return rulesets


def collect_tag_rulesets(
    repo: str, read: ReadApi, read_pages: ReadApi = read_gh_pages
) -> list[dict[str, object]]:
    """tag を対象にした有効なルールセットの詳細を集める。

    ルールセットの一覧の応答には rules が無く、tag の実効ルールを返す API も無いため、
    一覧から tag の有効なものを選んで詳細を読む。
    """
    details: list[dict[str, object]] = []
    for ruleset in list_rulesets(repo, read_pages):
        if ruleset["target"] != "tag" or ruleset["enforcement"] != "active":
            # branch 向けや evaluate・disabled のルールセットは、tag への署名の強制にならない
            continue
        detail = read(f"repos/{repo}/rulesets/{ruleset['id']}")
        if isinstance(detail, dict):
            details.append(detail)
    return details


def collect_tag_rules(
    repo: str, read: ReadApi, read_pages: ReadApi = read_gh_pages
) -> list[object]:
    """tag を対象にした有効なルールセットの rules をまとめる。"""
    rules: list[object] = []
    for detail in collect_tag_rulesets(repo, read, read_pages):
        detail_rules = detail.get("rules", [])
        if isinstance(detail_rules, list):
            rules.extend(detail_rules)
    return rules


def collect_tag_scope(
    repo: str, read: ReadApi, read_pages: ReadApi = read_gh_pages
) -> dict[str, list[str]]:
    """tag のルールセットが守る ref の範囲と、bypass できる相手をまとめる。

    ルールの種類だけでは、どの tag に効くか、誰が外せるかが分からないため、
    詳細の conditions の include・exclude と bypass_actors を並べて返す。
    """
    include: list[str] = []
    exclude: list[str] = []
    actors: list[str] = []
    for detail in collect_tag_rulesets(repo, read, read_pages):
        conditions = detail.get("conditions")
        ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
        if isinstance(ref_name, dict):
            include.extend(str(item) for item in ref_name.get("include", []))
            exclude.extend(str(item) for item in ref_name.get("exclude", []))
        bypass = detail.get("bypass_actors", [])
        actors.extend(
            f"{actor.get('actor_type')}:{actor.get('actor_id')}"
            for actor in (bypass if isinstance(bypass, list) else [])
            if isinstance(actor, dict)
        )
    return {"include": include, "exclude": exclude, "bypass_actors": actors}


def read_api(endpoint: str) -> object:
    if endpoint.endswith(TAG_RULES_SUFFIX):
        repo = endpoint.removesuffix(TAG_RULES_SUFFIX).removeprefix("repos/")
        return collect_tag_rules(repo, read_gh)
    if endpoint.endswith(TAG_SCOPE_SUFFIX):
        repo = endpoint.removesuffix(TAG_SCOPE_SUFFIX).removeprefix("repos/")
        return collect_tag_scope(repo, read_gh)
    return read_gh(endpoint)


def lookup(response: object, dotted_path: str) -> object:
    if ARRAY_MARK in dotted_path:
        # 配列（ルールの一覧、protection_rules など）は、各要素のそのキーを並べた配列にして、
        # 期待の値を含むかで比べる。"a.b[].c" は a.b の配列の各要素の c
        head, _, key = dotted_path.rpartition(ARRAY_MARK)
        array = lookup(response, head) if head else response
        if not isinstance(array, list):
            raise KeyError(dotted_path)
        return [item.get(key) for item in array if isinstance(item, dict)]
    value = response
    for key in dotted_path.split("."):
        if not isinstance(value, dict) or key not in value:
            raise KeyError(dotted_path)
        value = value[key]
    return value


def last_line(error: Exception) -> str:
    lines = str(error).strip().splitlines()
    return lines[-1] if lines else type(error).__name__


def find_differences(
    expected: dict[str, dict[str, object]], repo: str, read: ReadApi = read_api
) -> list[str]:
    differences: list[str] = []
    for endpoint_template, items in expected.items():
        endpoint = endpoint_template.format(repo=repo)
        try:
            response = read(endpoint)
        except (CommandError, json.JSONDecodeError, ResponseShapeError) as error:
            # 403・404 の項目も差に数える。読めないことを緑に見せないため
            differences.extend(
                f"{endpoint}: {path}: 期待 {wanted!r}、実際 読めない（{last_line(error)}）"
                for path, wanted in items.items()
            )
            continue
        for path, wanted in items.items():
            try:
                actual = lookup(response, path)
            except KeyError:
                differences.append(f"{endpoint}: {path}: 期待 {wanted!r}、実際 応答に無い")
                continue
            if ARRAY_MARK in path:
                # 期待が配列なら、その全ての要素を含むことを求める
                needed = wanted if isinstance(wanted, list) else [wanted]
                differences.extend(
                    f"{endpoint}: {path}: 期待 {item!r} を含む、実際 {actual!r}"
                    for item in needed
                    if item not in actual  # type: ignore[operator]
                )
                continue
            # True == 1 を同じと見ないよう、型まで含めて比べる
            if actual != wanted or type(actual) is not type(wanted):
                differences.append(f"{endpoint}: {path}: 期待 {wanted!r}、実際 {actual!r}")
    return differences


def main(
    argv: Sequence[str], read: ReadApi = read_api, settings_path: Path = SETTINGS_PATH
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--profile", required=True, choices=["working", "public"])
    arguments = parser.parse_args(argv)
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    differences = find_differences(settings[arguments.profile], arguments.repo, read)
    for difference in differences:
        print(difference, file=sys.stderr)
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
