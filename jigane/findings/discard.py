"""方針のレビューが捨てた候補と、捨てた割合の三状態。

runs.json の記録の読み方をここに置く。履歴（records.py）が捨てた割合を残すときも、
usage が出すときも、同じ読み方を共有する。
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, assert_never

_MIN_REASON_REST_LENGTH = 4

# 捨てた候補から写した nit の観点。通常の観点に無い値で、写した指摘の印になる
DISCARDED_VIEWPOINT = "discarded"
# 開示の場所を一つの文字列に並べるときの区切り（発見役の disclosed_at と版 2 の懸念の文）
PLACE_SEPARATOR = "、"

# 懸念の文が無い nit の主張の位置に出す一文。捨てた理由の文では代えない
NO_CONCERN_CLAIM = "この nit には懸念の文がありません（古い記録）"


class DiscardKind(StrEnum):
    """捨てた理由の種類。値は findings.json の discard_kind にそのまま保存される。"""

    UNVERIFIABLE_FROM_DIAGRAM = "unverifiable_from_diagram"
    NOT_A_CONTRACT = "not_a_contract"
    DEEPENING = "deepening"
    DECIDED_BY_DECLARATION = "decided_by_declaration"
    SETTLED_EARLIER = "settled_earlier"
    DISCLOSED_LIMITATION = "disclosed_limitation"
    OTHER = "other"

    @property
    def facts(self) -> "KindFacts":
        return DISCARD_KIND_FACTS[self]

    @property
    def label(self) -> str:
        """nit になった理由の札として出す名前。"""
        return self.facts.label

    @property
    def becomes_nit(self) -> bool:
        return self.facts.becomes_nit

    @property
    def sent_to_micro(self) -> bool:
        return self.facts.sent_to_micro


@dataclass(frozen=True)
class KindFacts:
    """捨てた理由の一つの種類について、並びに依らない性質を持つ。

    雛形の表の並びは DISCARD_REASONS、理由の文から種類を読み戻す並びは KIND_PATTERNS が持つ。
    どちらも種類をまたいで並ぶため、種類ごとの表には散らさない。
    """

    label: str
    # 直った論点は懸念が残らないので nit にならない
    becomes_nit: bool = True
    # 実装のレビューへ懸念として渡すか。図から確かめられない種類だけが渡す
    sent_to_micro: bool = False


# 値ごとの性質の唯一の置き場。ここに無い種類は、DiscardKind の全ての値を持つことを縛る試験が落とす
DISCARD_KIND_FACTS: dict[DiscardKind, KindFacts] = {
    DiscardKind.UNVERIFIABLE_FROM_DIAGRAM: KindFacts("図から確かめられない", sent_to_micro=True),
    DiscardKind.NOT_A_CONTRACT: KindFacts("契約にならない"),
    DiscardKind.DEEPENING: KindFacts("深掘り"),
    DiscardKind.DECIDED_BY_DECLARATION: KindFacts("宣言で決まっている"),
    DiscardKind.SETTLED_EARLIER: KindFacts("前の回で決着", becomes_nit=False),
    DiscardKind.DISCLOSED_LIMITATION: KindFacts("開示済みの限界"),
    DiscardKind.OTHER: KindFacts("その他"),
}


# 方針の群が捨てる理由（五つだけ）と、出力に書かせる種類の対応。雛形の表（macro_standards.md）は
# ここから生成して差し込み、出力の schema の enum は DiscardKind から作る。写しは持たない
DISCARD_REASONS: tuple[tuple[DiscardKind, str], ...] = (
    (DiscardKind.UNVERIFIABLE_FROM_DIAGRAM, "根拠の位置を示せない"),
    (DiscardKind.OTHER, "図と矛盾する"),
    (DiscardKind.DECIDED_BY_DECLARATION, "利用者の異議が付いた論点"),
    (DiscardKind.SETTLED_EARLIER, "前の回で決着した形を元へ戻させる"),
    # 直った論点と違い懸念が残るので、nit として示して開示が足りているかを人が確かめる。
    # 実装のレビューへは渡さない（開示は文書にあり、図から確かめられない種類ではない）
    (DiscardKind.DISCLOSED_LIMITATION, "既知の限界として開示済みの論点"),
)


def discard_reason_table() -> str:
    """捨てる理由と種類の対応を、雛形に差し込む Markdown の表にする。"""
    rows = ["| 捨てる理由 | kind |", "| --- | --- |"]
    rows.extend(f"| {reason} | {kind.value} |" for kind, reason in DISCARD_REASONS)
    return "\n".join(rows)


# 理由の文の言い回しから種類を決める表。上の行ほど先に当てる。
# 一つの文が複数の言い回しを含むときは、先に当たった種類にする。
# 図から確かめられない種類は、実装のレビューへ渡す懸念になる唯一の種類なので、
# 契約として小さいとも書いた文を先に契約の側へ寄せ、渡す懸念を絞る
KIND_PATTERNS: tuple[tuple[DiscardKind, re.Pattern[str]], ...] = (
    (DiscardKind.DISCLOSED_LIMITATION, re.compile(r"既知の限界|限界として(開示|書か|明記)")),
    (
        DiscardKind.DEEPENING,
        re.compile(r"深掘り|問い直す往復|出し直さない|重ねて出さない|再び問う"),
    ),
    (
        DiscardKind.SETTLED_EARLIER,
        re.compile(
            r"前の回|決着(済み)?|直った|直し(た|て)(形|いる)|直す方向|解消(済み|された)|"
            r"既に(直|解消)|テスト[^。]{0,20}で守られ|検査[^。]{0,20}で守られ"
        ),
    ),
    (
        DiscardKind.DECIDED_BY_DECLARATION,
        re.compile(
            r"決定済み|蒸し返|意思決定ログ|"
            r"宣言[^。]{0,40}(決め|選ん|求め|不変|としている|明示|定め|示し|書い|沿っ|目的|"
            r"と一致|矛盾|の外|述べ|限っ|と宣言|宣言さ)"
        ),
    ),
    (
        # 図から確かめられないと書いても、契約として小さいとも書いた理由は、実装へ渡す懸念にしない
        DiscardKind.NOT_A_CONTRACT,
        re.compile(
            r"手戻り[^。]{0,12}(小さ|大きくな|大きいと)|契約[^。]{0,8}(現れ|越え|崩さ|ならない|当たらない|になる形)|"
            r"局所|閉じ(て|た)|費用は小さ|非公開|変更前から|"
            r"手戻り[^。]{0,12}大きい(?!か)|欠陥には当たらない|契約の形でもない|指摘にしない|"
            r"新たに(崩|作)|範囲外|範囲の外"
        ),
    ),
    (
        DiscardKind.UNVERIFIABLE_FROM_DIAGRAM,
        re.compile(
            r"((?<![意構])図|資料)[^。]{0,40}?"
            r"(確かめ|問え|読め|読み取れ|決め|現れ|出|見え|示せ|言え|判断でき|断定でき|分から|追え)"
            r"(られ|て|てい|ておら)?(ない|無い|ず|ぬ)|"
            r"(根拠|事実|支え|証拠|道筋)(が|は)?図に(は)?無|"
            r"(?<![意構])図に(は)?[^。]{0,16}(根拠|事実|支え|証拠|道筋)が無|"
            r"(?<![意構])図[^。]{0,80}(確かめ|言え|言い切れ|読め|断定でき|決め)(られ)?(ない|ず)|"
            r"本体の内容に頼|"
            r"根拠[^。]{0,10}(示せ|足り)"
        ),
    ),
    (
        DiscardKind.NOT_A_CONTRACT,
        re.compile(
            r"契約|手戻り|局所|内部|閉じ(て|た)|公開(の|で)[^。]{0,6}ない|"
            r"シグネチャの変更は無|保存[^。]{0,6}(物|の形)[^。]{0,10}(現れ|変わ|響か)|"
            r"直す(費用|場所)|指摘にしない|指摘にならない|出さない|捨てた|"
            r"対象外|受け持ち|変更前から|変わっていない|欠陥には当たらない|成り立たない|"
            r"命名の話|文言の話|綻びは無い|より前から|新たに作った|触れていない|費用は小さ|"
            r"にとどまる|非公開|境を越え|別の概念"
        ),
    ),
)


# 理由の文の頭にある主題は、最初の「は、」までとする。主題には括弧の中の「、」も含む。
# 最初の「は、」が助詞で終わる語（「図では、」「からは、」のような）に付く文は、
# 主題を取らない。先の「は、」まで伸ばすと、文頭の副詞句を主題に取り違えるため
_TOPIC_MARKER = "は、"
_PARTICLE_ENDINGS = (
    "で",
    "に",
    "と",
    "が",
    "を",
    "も",
    "へ",
    "は",
    "の",
    "、",
    "。",
    "から",
    "まで",
    "より",
)


# 「は、」が無い文で、主題を最初の「は」までとするときの長さの上限。
# これを超える前置きは、主題でなく文頭の長い節と見なして取らない。
# 短すぎる語（「宣言」のような）は、何が懸念かを伝えないので主題に取らない
_BARE_TOPIC_LIMIT = 60
_BARE_TOPIC_MINIMUM = 6


def _topic_of(reason: str) -> str:
    marker = reason.find(_TOPIC_MARKER)
    if marker >= 0:
        return reason[:marker].rstrip()
    # 「は、」で区切られない文は、読点の無い「は」までを主題にする。読点を越えると節を取り違える
    bare = reason.find("は")
    if (
        _BARE_TOPIC_MINIMUM <= bare <= _BARE_TOPIC_LIMIT
        and "、" not in reason[:bare]
        and "。" not in reason[:bare]
    ):
        return reason[:bare].rstrip()
    return ""


# 述語の側に図や断りの言い回しが混じるときは、述べている事柄でなく理由の側なので取らない
_REASON_WORDS = re.compile(
    r"図|資料|確かめ|読み取れ|言え|問え|捨て|出さない|蒸し返|宣言|当たらない"
)
# 述語が接続で終わる文は、事柄を言い切っていない（「話で、」のような理由の前半）ので取らない
_UNFINISHED_ENDINGS = (
    "で",
    "ので",
    "ため",
    "が",
    "て",
    "し",
    "ず",
    "も",
    "は",
    "に",
    "を",
    "と",
    "の",
)


def _predicate_sentence(reason: str, topic: str) -> str | None:
    """主題に続く述語まで含めた先頭の一文。事柄を言い切った文でなければ None。

    「A は B を載せ、C は D を無視する。」のように、主題だけでは事柄が落ちる文のためにある。
    """
    if reason.find(_TOPIC_MARKER) >= 0:
        # 「は、」で区切られた主題は、述語でなく理由が続く形なので主題だけを取る
        return None
    sentence = reason.split("。", 1)[0].strip()
    rest = sentence[len(topic) :]
    if (
        len(rest) < _MIN_REASON_REST_LENGTH
        or _REASON_WORDS.search(rest)
        or sentence.endswith(_UNFINISHED_ENDINGS)
    ):
        return None
    return sentence + "点に懸念がある。"


def _topic_sentence(topic: str) -> str:
    """主題（名詞句）を、懸念を述べる一文にする。"""
    if topic.endswith(("懸念", "候補", "指摘")):
        return topic + ("がある。" if topic.endswith("懸念") else "に懸念がある。")
    if topic.endswith("点"):
        return topic + "が懸念である。"
    if topic.endswith(("疑い", "おそれ", "恐れ", "可能性")):
        return topic + "がある。"
    return topic + "に懸念がある。"


# 懸念の文として取り出してよい文の条件を判定する唯一の関数。
# 対象に加えて懸念の語を含むこと、接続で終わって途切れていないことを求める。
# 満たさない文は取り出せなかったものとして扱われ、元の文が主張になる
_CONCERN_WORDS = ("懸念", "恐れ", "おそれ", "疑い", "可能性")
_CONNECTIVE_ENDINGS = ("であり", "り", "く", "つつ", "けれど", "けど")


def is_concern_sentence(text: str) -> bool:
    body = text.strip().removesuffix("。").rstrip("、, ")
    if not any(word in body for word in _CONCERN_WORDS):
        return False
    return not body.endswith(_UNFINISHED_ENDINGS + _CONNECTIVE_ENDINGS)


def _topic_concern(reason: str) -> str | None:
    """文頭の主題を懸念の文にする。主題に述語が続いて事柄を言い切る文は、その文を取る。

    主題を取り出せない文は None。
    """
    topic = _topic_of(reason)
    if not topic or topic.endswith(_PARTICLE_ENDINGS):
        return None
    predicate = _predicate_sentence(reason, topic)
    if predicate is not None:
        return predicate
    return _topic_sentence(topic)


# 理由の文の末尾にある断り。「なので出さない」「指摘にしない」「捨てる」のように、
# 候補を出さない結論を述べる節
_REFUSAL = (
    r"(?:出し直さない|重ねて出さない|蒸し返さない|蒸し返さず|蒸し返しになる|蒸し返しは避ける|出さない|指摘にしない|指摘にならない|指摘にはならない|"
    r"指摘には当たらない|指摘に当たらない|捨てる|捨てた)"
)
_TRAILING_REFUSAL = re.compile(r"[、,][^、。]*" + _REFUSAL + r"$")
# 読点を挟まず接続の語で断りに続く形（「宣言で決まっているので蒸し返さない」）。
# 断りと接続の語を外す
_TRAILING_BARE_REFUSAL = re.compile(r"(?:なので|ので|ため|から|ゆえ)?" + _REFUSAL + r"$")
# 宣言や決定済みを理由に挙げる末尾の節。懸念でなく断りの根拠なので外す
_TRAILING_DECLARATION = re.compile(
    r"[、,][^、。]*(?:宣言|決定済み|意思決定ログ|決まっている|決めている)[^、。]*$"
)
_REFUSAL_SENTENCE = re.compile(_REFUSAL + r"$|(?:受け持ち|範囲外|対象外|当たらない)[^。]*$")


# 名詞句で終わる取り出し（「…形」「…案」）は、そのままでは懸念を述べないので主題の形に整える
_NOUN_PHRASE_ENDINGS = ("形", "案", "点", "穴", "構成", "方式", "設計", "置き方", "持ち方")


def _without_declaration_clause(sentence: str) -> str:
    """宣言を理由に挙げる末尾の節を外す。外すと主題だけが残る文は、外さずに残す。"""
    cut = _TRAILING_DECLARATION.sub("", sentence)
    return sentence if cut.endswith(("は", "が", "も")) else cut


def _without_refusal_clause(sentence: str) -> str | None:
    """文末の断りの節を外した文。懸念を述べず断りだけの文は None。"""
    trimmed = _TRAILING_REFUSAL.sub("", sentence)
    if trimmed == sentence:
        trimmed = _TRAILING_BARE_REFUSAL.sub("", sentence)
    if trimmed != sentence:
        return trimmed.rstrip("、, ").removesuffix("は").removesuffix("が").removesuffix("も")
    if _REFUSAL_SENTENCE.search(sentence):
        return None
    # 断りの無い文でも、読点で途切れた末尾は接続の判定にかかるよう落とす
    return trimmed.rstrip("、, ")


def _concern_of_trimmed(sentence: str, trimmed: str) -> str | None:
    """断りの節を外した文を懸念の文に整える。主題が取れず接続で終わる文は None。"""
    if trimmed.endswith(_UNFINISHED_ENDINGS + _CONNECTIVE_ENDINGS):
        # 断りや宣言の節を外した残りが接続で終わるときは、その節ごと外して主題に戻す
        topic = _topic_of(sentence)
        if not topic or topic.endswith(_PARTICLE_ENDINGS):
            return None
        return _topic_sentence(topic)
    if trimmed.endswith(_NOUN_PHRASE_ENDINGS):
        return _topic_sentence(trimmed)
    return trimmed + "。"


def _sentence_concern(raw_sentence: str, *, strip_declaration: bool) -> str | None:
    """理由の一文から懸念の文を取る。取れない文は None で、次の文へ進ませる。"""
    sentence = _without_declaration_clause(raw_sentence) if strip_declaration else raw_sentence
    trimmed = _without_refusal_clause(sentence)
    if trimmed is None or len(trimmed) < _BARE_TOPIC_MINIMUM:
        return None
    return _concern_of_trimmed(sentence, trimmed)


def _clause_concern(reason: str, *, strip_declaration: bool = False) -> str | None:
    """理由の文から断りの部分を外し、先頭の一文を懸念の文にする。残らなければ None。

    断りだけの文は落とし、文末に断りの節が付く文はその節を外す。
    strip_declaration のときは、宣言を理由に挙げる末尾の節も外す。
    """
    for part in reason.split("。"):
        concern = _sentence_concern(part.strip(), strip_declaration=strip_declaration)
        if concern is not None:
            return concern
    return None


def _concern_of(reason: str, kind: DiscardKind) -> str | None:
    """明示の concern が無い出力の読み戻し専用。理由の文から懸念の文を取る。取れなければ None。

    図から確かめられない種類は、文頭の主題だけを残して語尾を整える（「図に現れていないため問えない」の
    断りと、その後ろの理由は落とす）。ほかの種類は、文末の断りの節（「なので出さない」など）を外した
    先頭の一文を取る。いずれも元の文を書き直さず、取り出せない文は None を返す。
    """
    concern = _raw_concern_of(reason, kind)
    return concern if concern is not None and is_concern_sentence(concern) else None


def _checked_concern(concern: str) -> str | None:
    return concern if is_concern_sentence(concern) else None


def _raw_concern_of(reason: str, kind: DiscardKind) -> str | None:
    if kind is DiscardKind.UNVERIFIABLE_FROM_DIAGRAM:
        return _topic_concern(reason)
    if kind is DiscardKind.DECIDED_BY_DECLARATION:
        # 理由の文がそのまま主張になりやすい種類。「という候補」は主題、他は宣言の節と断りを外す
        topic = _topic_of(reason)
        if topic.endswith(("候補", "指摘", "懸念")):
            # 長い「という候補」の主題は、後ろの理由の節を引きずらず主題で言い切る
            return _topic_concern(reason)
        return _clause_concern(reason, strip_declaration=True) or _clause_concern(reason)
    return _clause_concern(reason)


@dataclass(frozen=True)
class ExtractedConcern:
    """理由の文から取り出せた懸念の文。"""

    text: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("懸念の文が空の ExtractedConcern は作れません")


KEY_FROM_CONCERN = "from_concern"
"""指摘が受けた懸念の番号を書く findings.json の欄。解き方は PassedConcern の契約に従う。"""


@dataclass(frozen=True)
class PassedConcern:
    """発見役の起動のプロンプトに懸念として渡した、方針の指摘の番号と文。

    runs.json の invocation が ShardConcerns.passed として持つ。

    懸念への参照の契約（findings.json の KEY_FROM_CONCERN と runs.json の PassedConcern.id）:

    - 参照は番号であり、渡した時点の方針の指摘を指す。番号が指す先は、その起動の
      PassedConcern の列（番号と文）だけで解く。現在の findings.json の方針の指摘を引いて解かない。
    - 方針の群を走らせ直して方針の指摘が置き換わると、同じ番号が別の指摘を指しうる。そのため
      参照の寿命は渡した起動の記録が持つ文までで、置き換わったあとの方針の指摘には及ばない。
      置き換わったあとは、記録に残した文を、受けた懸念の内容として読む。
    - 起動が渡していない番号を指摘が書いたら、参照として採らず、落とす。
    """

    id: str
    text: str


@dataclass(frozen=True)
class ShardConcerns:
    """シャードのプロンプトに渡す懸念。シャードごとに一度だけ組み立て、以後は変えない。

    プロンプトの懸念の節と runs.json の記録は、この値だけから引く。
    """

    passed: tuple[PassedConcern, ...] = ()
    # 懸念の文を取り出せず、件数の一行にまとめた件数。passed には入らない
    unspecified: int = 0


@dataclass(frozen=True)
class NoConcern:
    """理由の文から懸念の文を取り出せなかったこと。"""


class _NitReasonBase:
    """nit の理由の二つの型が共有する導出。kind、text、concern を持つ型だけが継ぐ。"""

    kind: DiscardKind
    text: str
    concern: "ExtractedConcern | NoConcern"

    @property
    def sent_to_micro(self) -> bool:
        return self.kind.sent_to_micro

    @property
    def claim(self) -> str:
        """nit の主張。懸念の文を取り出せればそれ、できなければ元の文。"""
        match self.concern:
            case ExtractedConcern(text=concern):
                return concern
            case NoConcern():
                return self.text

    def _json(self, places: tuple[str, ...]) -> dict[str, Any]:
        concern = self.concern.text if isinstance(self.concern, ExtractedConcern) else None
        return {
            "kind": self.kind.value,
            "text": self.text,
            "concern": concern,
            "places": list(places),
        }


@dataclass(frozen=True)
class DisclosedNitReason(_NitReasonBase):
    """開示済みの限界として nit になった理由。開示の場所を持てる唯一の型。

    種類は常に DISCLOSED_LIMITATION である。方針の群が捨てた候補から写す開示済みの限界は
    場所を述べないので、places は空でもよい。
    """

    text: str
    concern: "ExtractedConcern | NoConcern"
    places: tuple[str, ...] = ()

    @property
    def kind(self) -> DiscardKind:  # type: ignore[override]
        return DiscardKind.DISCLOSED_LIMITATION

    def to_json(self) -> dict[str, Any]:
        return self._json(self.places)


@dataclass(frozen=True)
class OrdinaryNitReason(_NitReasonBase):
    """開示済みの限界以外の種類で nit になった理由。開示の場所を持たない。"""

    kind: DiscardKind
    text: str
    concern: "ExtractedConcern | NoConcern"

    def __post_init__(self) -> None:
        if not self.kind.becomes_nit:
            # 直った論点は懸念が残らないので nit にならない。NotNit(SETTLED_EARLIER) で表す
            raise ValueError(f"{self.kind.name} の NitReason は作れません")
        if self.kind is DiscardKind.DISCLOSED_LIMITATION:
            raise ValueError("開示済みの限界は DisclosedNitReason で表します")

    def to_json(self) -> dict[str, Any]:
        # 保存の形は種類によらず places の鍵を持つ。この型では常に空
        return self._json(())


# 捨てた候補が nit になった理由。findings.json の nit_reason 欄の中身で、返信には載せない。
# 種類、元の文、懸念の文の三つが正で、実装のレビューへ渡したかは種類から導く。
# nit になるかどうかは nit_of が返す NitDecision だけが決め、なるときの型は主張の型から写す。
NitReason = DisclosedNitReason | OrdinaryNitReason


def nit_reason_from_json(raw: dict[str, Any]) -> NitReason:
    """保存物の nit_reason 欄から二つの型の一方を読む。"""
    kind = DiscardKind(raw["kind"])
    concern = raw["concern"]
    resolved = NoConcern() if concern is None else ExtractedConcern(concern)
    places = tuple(raw["places"])
    return nit_reason_of_claim(raw["text"], claim_of_kind(kind, resolved, places))


def places_line(reason: NitReason) -> str | None:
    """開示の場所を一行にした文「開示の場所: A、B」。場所が無ければ None を返す。

    場所の文の唯一の出所。concern_with_places と画面のカードが、この一行を使う。
    """
    match reason:
        case DisclosedNitReason(places=places) if places:
            return f"開示の場所: {PLACE_SEPARATOR.join(places)}"
        case DisclosedNitReason() | OrdinaryNitReason():
            return None


def concern_with_places(reason: NitReason) -> str | None:
    """画面と返信に出す懸念の文。開示の場所があれば「（開示の場所: A、B）」を添える。

    この形の唯一の出所。保存物の concern は主張だけで、場所は places に分けて持つ。
    懸念の文が無いときは None を返す。元の文は捨てた理由の文なので、懸念の文の代わりに出さない。
    """
    match reason.concern:
        case NoConcern():
            return None
        case ExtractedConcern():
            line = places_line(reason)
            return reason.claim if line is None else f"{reason.claim}（{line}）"


class NotNitKind(StrEnum):
    """捨てた候補が nit にならない理由の種類。"""

    SETTLED_EARLIER = "settled_earlier"
    EMPTY_CLAIM = "empty_claim"


@dataclass(frozen=True)
class Nit:
    """捨てた候補を nit にする判定。"""

    reason: NitReason


@dataclass(frozen=True)
class NotNit:
    """捨てた候補を nit にしない判定。"""

    reason: NotNitKind


NitDecision = Nit | NotNit


@dataclass(frozen=True)
class DisclosedClaim:
    """開示済みの限界の主張。開示の場所を持てる唯一の主張で、場所は空でもよい。"""

    concern: ExtractedConcern | NoConcern
    places: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrdinaryClaim:
    """開示済みの限界以外の種類の主張。開示の場所を持たない。"""

    kind: DiscardKind
    concern: ExtractedConcern | NoConcern

    def __post_init__(self) -> None:
        if self.kind is DiscardKind.DISCLOSED_LIMITATION:
            raise ValueError("開示済みの限界の主張は DisclosedClaim で作ります")


# 捨てた候補の主張。種類から型を決める分岐は claim_of_kind だけが持つ
DiscardClaim = DisclosedClaim | OrdinaryClaim


def claim_of_kind(
    kind: DiscardKind,
    concern: ExtractedConcern | NoConcern,
    places: tuple[str, ...],
) -> DiscardClaim:
    """種類から主張の型を決める。開示の場所を持てるのは開示済みの限界だけである。"""
    if kind is DiscardKind.DISCLOSED_LIMITATION:
        return DisclosedClaim(concern, places)
    if places:
        raise ValueError(f"{kind.name} の主張は開示の場所を持てません")
    return OrdinaryClaim(kind, concern)


def discard_claim(
    reason: str,
    kind: DiscardKind | None,
    concern: str | None,
    disclosed_at: str | None = None,
) -> DiscardClaim:
    """方針の群の出力にある明示の項目と理由の文から、種類ごとの型の主張を確定する。

    種類と懸念が None なのは、項目を持たない古い保存物か、項目を欠いたモデルの出力である。
    その分だけ文から正規表現で読み戻す。明示の懸念も is_concern_sentence を満たさなければ
    取り出せなかったものとして扱う。None の解釈と読み戻しは、ここから外へ出さない。
    """
    resolved_kind = discard_kind_of(reason) if kind is None else kind
    text = _concern_of(reason, resolved_kind) if concern is None else _checked_concern(concern)
    resolved = NoConcern() if text is None else ExtractedConcern(text)
    # 開示の場所を持てるのは開示済みの限界だけで、
    # ほかの種類でモデルが付けた disclosed_at はここで一度だけ読み捨てる
    is_disclosed = resolved_kind is DiscardKind.DISCLOSED_LIMITATION
    places = places_of(disclosed_at) if is_disclosed and disclosed_at else ()
    return claim_of_kind(resolved_kind, resolved, places)


def places_of(disclosed_at: str) -> tuple[str, ...]:
    """disclosed_at の文を開示の場所に分ける。区切りは PLACE_SEPARATOR で、空の場所は捨てる。"""
    return tuple(place for place in disclosed_at.split(PLACE_SEPARATOR) if place)


def nit_reason_of_claim(reason: str, claim: DiscardClaim) -> NitReason:
    """主張の型から nit の理由の型を写す。開示の場所は DisclosedClaim だけが運ぶ。"""
    match claim:
        case DisclosedClaim(concern=concern, places=places):
            return DisclosedNitReason(reason, concern, places)
        case OrdinaryClaim(kind=kind, concern=concern):
            return OrdinaryNitReason(kind, reason, concern)
        case _:
            assert_never(claim)


def nit_of(reason: str, claim: DiscardClaim) -> NitDecision:
    """捨てた理由の文と確定済みの主張から、nit にするかどうかを決める。入口はここだけである。

    前の回の指摘が直ったという理由は、懸念が残っていないので nit にしない。
    懸念の文も元の文も空の候補は、主張が空になるので nit にしない。
    """
    if isinstance(claim, OrdinaryClaim) and not claim.kind.becomes_nit:
        return NotNit(NotNitKind.SETTLED_EARLIER)
    nit = nit_reason_of_claim(reason, claim)
    if not nit.claim.strip():
        return NotNit(NotNitKind.EMPTY_CLAIM)
    return Nit(nit)


CONCERNS_HEADING = "確かめてほしい懸念"
CONCERNS_INTRO = (
    "方針のレビューが図から確かめられず、実装のレビューへ回した懸念である。"
    "コードで確かめ、問題があれば指摘にする。"
    "この懸念から指摘を作ったときは、from_concern に方針の番号（例 F2）を書く。"
)


def discard_kind_of(reason: str) -> DiscardKind:
    """明示の kind が無い出力の読み戻し専用。理由の文から種類を見分ける。当たらない文は OTHER。"""
    for kind, pattern in KIND_PATTERNS:
        if pattern.search(reason):
            return kind
    return DiscardKind.OTHER


@dataclass(frozen=True)
class DiscardedCandidate:
    """モデルが捨てた候補。source は S1 のような通し番号で、reason は捨てた理由の一文。

    kind と concern は、モデルが明示の項目で返した捨てた種類と懸念の一文。項目を持たない
    古い保存物とモデルの出力では None で、読み戻しは discard_claim の正規表現が受け持つ。
    disclosed_at は開示済みの限界の開示の場所で、モデルが任意に返す。
    """

    source: str
    reason: str
    kind: DiscardKind | None = None
    concern: str | None = None
    disclosed_at: str | None = None

    def decision(self) -> NitDecision:
        """この候補を nit にするかどうか。生の項目はここで一度だけ主張へ確定して渡す。"""
        claim = discard_claim(self.reason, self.kind, self.concern, self.disclosed_at)
        return nit_of(self.reason, claim)

    def to_json(self) -> dict[str, str]:
        stored = {"source": self.source, "reason": self.reason}
        if self.kind is not None:
            stored["kind"] = self.kind.value
        if self.concern is not None:
            stored["concern"] = self.concern
        if self.disclosed_at is not None:
            stored["disclosed_at"] = self.disclosed_at
        return stored

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "DiscardedCandidate":
        kind = value.get("kind")
        concern = value.get("concern")
        disclosed_at = value.get("disclosed_at")
        return cls(
            str(value["source"]),
            str(value["reason"]),
            None if kind is None else DiscardKind(kind),
            None if concern is None else str(concern),
            None if disclosed_at is None else str(disclosed_at),
        )


@dataclass(frozen=True)
class Considered:
    """モデルが検討した候補の結果。discovered は検討した件数で、discarded はそのうち捨てた候補。"""

    discovered: int
    discarded: tuple[DiscardedCandidate, ...]

    @property
    def kept(self) -> int:
        return self.discovered - len(self.discarded)

    def to_json(self) -> dict[str, Any]:
        return {
            "discovered": self.discovered,
            "discarded": [item.to_json() for item in self.discarded],
        }


class DiscardState(StrEnum):
    """捨てた割合について言える三つの状態。値は usage の JSON と CSV にそのまま出る。"""

    UNRECORDED = "unrecorded"
    NONE_DISCOVERED = "none_discovered"
    MEASURED = "measured"


@dataclass(frozen=True)
class DiscardSummary:
    """捨てた割合の三状態。数は MEASURED のときだけ持ち、ほかの状態では持てない。"""

    state: DiscardState
    discovered: int | None = None
    discarded: int | None = None
    ratio: float | None = None

    def __post_init__(self) -> None:
        carries_numbers = (self.discovered, self.discarded, self.ratio) != (None, None, None)
        if carries_numbers != (self.state is DiscardState.MEASURED):
            raise ValueError(f"数を持てるのは MEASURED だけです: {self}")


def discard_summary(considered: Considered | None) -> DiscardSummary:
    """検討の結果から、捨てた割合の状態を決める。None は古い記録で、何も言えない。"""
    if considered is None:
        return DiscardSummary(DiscardState.UNRECORDED)
    return summary_of_counts(considered.discovered, len(considered.discarded))


def summary_of_counts(discovered: int, discarded: int) -> DiscardSummary:
    """記録のある回の、発見と捨てた数から状態を決める。履歴は数だけを残し、ここで読み戻す。"""
    if discovered == 0:
        # 割る数が無い。記録が無いこととは別に、何も検討しなかった回として区別する
        return DiscardSummary(DiscardState.NONE_DISCOVERED)
    return DiscardSummary(DiscardState.MEASURED, discovered, discarded, discarded / discovered)
