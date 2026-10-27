"""方針の資料に入る文が、表示用の文を敬体にしても変わらないことを固定する。"""

from typing import Any, cast

from jigane.figures.models import NoteText, SequenceFigure
from jigane.regex.tags import CountedRegex, CountedTag, Rule, Severity, ShapeTag
from jigane.sequence import review_diagrams
from jigane.skills.policy_regex import _directions

DIRECTIONS = {
    Rule.R1: "一部を普通のコードに置き換えるか、複数の正規表現に分ける。",
    Rule.R2: "分岐はプログラム言語の側へ移し、正規表現ごとに分けて if や表で振り分ける。",
    Rule.R3: "条件はコードで一つずつ確かめる。",
    Rule.R4: "内側を名前のある正規表現に分ける。",
    Rule.R5: "名前つきの組にするか、捕獲した後の処理をコードで書く。",
    Rule.R6: "文字列の置き換えや比較のメソッドを使う。",
    Rule.R7: "量指定子を外すか、占有の形や原子の組を使う。",
}


def _tag(rule: Rule) -> CountedTag | ShapeTag:
    if rule in (Rule.R6, Rule.R7):
        return ShapeTag(rule, Severity.NIT)
    return CountedTag(rule, Severity.NIT, 3)


def test_regex_directions_in_the_material_keep_the_plain_form() -> None:
    for rule, direction in DIRECTIONS.items():
        verdict = CountedRegex(cast("Any", None), (_tag(rule),))
        assert _directions([verdict]) == f"直し方の向き: {rule} は、{direction}"


def test_sequence_notes_in_the_material_keep_the_plain_form() -> None:
    notes = review_diagrams
    assert notes.NO_ADAPTER_TEXT == "変更したファイルの言語に、呼び出しの抽出がまだ無い。"
    assert notes.NO_DECLARATION_TEXT == "変更した宣言も、変更したファイルを読む宣言も無い。"
    assert notes.NO_BASE_TREE_TEXT == "変更前のツリーが無いため、変更前の図は作れない。"
    assert notes.NO_INTERACTION_TEXT == "変更した宣言はあるが、他の型とのやり取りが無い。"
    assert notes.BEFORE_NO_ROOT_NOTE == "変更前にこの入口が無い"
    assert notes.BEFORE_NO_PATH_NOTE == "変更前は変更した宣言へ届かない"
    assert notes.AFTER_NO_ARROW_NOTE == "変更後の図に呼び出しが無い"
    assert notes.NO_CALL_ENTRIES_NOTE == "呼び出しの無い入口 {count} 件は図にしない: {names}"
    assert (
        notes.MARKUP_CALLED_ENTRIES_NOTE == "XAML から呼ばれる入口 {count} 件は図にしない: {names}"
    )
    assert notes.UNRELATED_CALL_ENTRIES_NOTE == (
        "変更に関わる呼び出しの無い入口 {count} 件は図にしない: {names}"
    )
    assert notes.UNRESOLVED_CALL_ENTRIES_NOTE == (
        "呼び先を解けない呼び出しがある入口 {count} 件は図にしない: {names}"
    )
    assert notes.DROPPED_CHANGES_NOTE == "入口として拾えなかった変更 {count} 件: {names}"
    figure = SequenceFigure(
        (),
        (
            NoteText(
                "変更した宣言はあるが、他の型とのやり取りが無い。",
                "変更した宣言はありますが、他の型とのやり取りがありません。",
            ),
        ),
    )
    assert figure.text() == "変更した宣言はあるが、他の型とのやり取りが無い。"
