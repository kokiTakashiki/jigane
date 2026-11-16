import os
import re
import stat
import sys
from dataclasses import dataclass, field, fields
from enum import Enum, StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NamedTuple, assert_never

# GitHub の owner と repo に使える文字（英数字、-、_、.）に限る。先頭は . と - を除く。
# `.` と `..` だけの部分や `.` で始まる部分がデータの置き場の外を指すのを防ぐ
COMPONENT_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
_NUMBER_PATTERN = re.compile(r"[0-9]+")


def default_data_home() -> Path:
    """JIGANE_HOME を無視した既定の置き場。"""
    if sys.platform == "win32":
        # LOCALAPPDATA が無い環境は想定しない。無ければ KeyError で気づかせる
        return Path(os.environ["LOCALAPPDATA"]) / "jigane"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "jigane"


def data_home() -> Path:
    override = os.environ.get("JIGANE_HOME")
    if override:
        return Path(override)
    return default_data_home()


def require_private_data_home(home: Path) -> None:
    """既にある根の権限が広すぎれば ReviewError で止める。

    根の所有者が実行ユーザーでない、または group か other に書き込み権限がある場合が対象。
    読み取りの権限だけなら止めない。根が symlink のときは、その先を調べる。
    Windows は ACL に任せ、検査しない。
    """
    if sys.platform == "win32":
        # POSIX の所有者と mode を持たないため、権限は ACL に任せる
        return
    info = home.stat()
    if info.st_uid != os.getuid():
        # 所有者が違うと実行ユーザーでは chmod できないため、chmod の案内とは文面を分ける
        raise ReviewError(
            "データの置き場の所有者が実行ユーザーと違います。"
            "所有者のユーザーで実行するか、置き場を自分の所有に移してください"
        )
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ReviewError("データの置き場の権限が広すぎます。chmod 700 で狭めてください")


def ensure_data_home() -> Path:
    """データの置き場の根が無ければ 0700 で作って返し、既にあれば権限を検査する。

    置き場には差分、PR の本文、Claude の出力が平文で入るため、他の利用者に書かせない。
    既にある根の権限は変えず、広すぎるときは require_private_data_home が止める。
    """
    home = data_home()
    if home.exists():
        require_private_data_home(home)
        return home
    home.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 作った瞬間から広い権限で見えないよう、mode を渡して作る
        home.mkdir(mode=0o700)
    except FileExistsError:
        # 同時に作った側の根の権限には触らない
        return home
    # mkdir の mode は umask で削られるので、作ったあとにも明示する。
    # Windows の chmod は読み取り専用の印にしか効かず、0o700 を渡しても害はない
    home.chmod(0o700)
    return home


class ReviewError(Exception):
    """レビューの読み書きで、利用者へそのまま伝える失敗。"""


class ReviewKind(StrEnum):
    """レビューの出所の種類。識別子の区切り、キーの規則、保存先はこの種類の性質として持つ。"""

    PULL_REQUEST = "pull_request"
    RANGE = "range"
    FEATURE = "feature"
    DESIGN_DOC = "design_doc"

    @property
    def separator(self) -> str:
        match self:
            case ReviewKind.PULL_REQUEST:
                return "#"
            case ReviewKind.RANGE:
                return "@"
            case ReviewKind.FEATURE:
                return ":"
            case ReviewKind.DESIGN_DOC:
                # シェルの履歴展開（!）やチルダ展開、URL の予約文字と重ならず、
                # GitHub の owner と repo に使えない文字でもある
                return "+"
            case _:
                assert_never(self)

    @property
    def key_pattern(self) -> re.Pattern[str]:
        match self:
            case ReviewKind.PULL_REQUEST:
                return _NUMBER_PATTERN
            case ReviewKind.RANGE:
                return COMPONENT_PATTERN
            case ReviewKind.FEATURE | ReviewKind.DESIGN_DOC:
                return COMPONENT_PATTERN
            case _:
                assert_never(self)

    @property
    def label(self) -> str:
        """利用者に見せる種類の呼び名。"""
        match self:
            case ReviewKind.PULL_REQUEST:
                return "PR"
            case ReviewKind.RANGE:
                return "範囲"
            case ReviewKind.FEATURE:
                return "ファイル"
            case ReviewKind.DESIGN_DOC:
                return "設計書"
            case _:
                assert_never(self)

    @property
    def is_pull_request(self) -> bool:
        """PR 固有の動作（ボットの材料）をするか。refresh は PR に固有でない。

        reply は差分のレビューで文を作る。
        """
        match self:
            case ReviewKind.PULL_REQUEST:
                return True
            case ReviewKind.RANGE | ReviewKind.FEATURE | ReviewKind.DESIGN_DOC:
                return False
            case _:
                assert_never(self)

    @property
    def externally_numbered(self) -> bool:
        """キーを外部（GitHub）が採番するか。名前で作る種類は、この種類の保存先と重ならないよう譲る。"""
        match self:
            case ReviewKind.PULL_REQUEST:
                return True
            case ReviewKind.RANGE | ReviewKind.FEATURE | ReviewKind.DESIGN_DOC:
                return False
            case _:
                assert_never(self)

    @property
    def is_diff(self) -> bool:
        """差分のレビューか。"""
        match self:
            case ReviewKind.PULL_REQUEST | ReviewKind.RANGE:
                return True
            case ReviewKind.FEATURE | ReviewKind.DESIGN_DOC:
                return False
            case _:
                assert_never(self)

    @property
    def mode(self) -> str:
        """review.json に書く mode の文字列。"""
        match self:
            case ReviewKind.PULL_REQUEST | ReviewKind.RANGE:
                return "diff"
            case ReviewKind.FEATURE | ReviewKind.DESIGN_DOC:
                return "feature"
            case _:
                assert_never(self)

    @property
    def directory_prefix(self) -> str:
        """キーの前に付けるディレクトリ名の印。

        範囲と設計書は PR の番号やファイルのレビューの名前と重ならないよう、
        識別子の区切りと同じ印を付ける。
        """
        match self:
            case ReviewKind.PULL_REQUEST | ReviewKind.FEATURE:
                return ""
            case ReviewKind.RANGE:
                return "@"
            case ReviewKind.DESIGN_DOC:
                return "+"
            case _:
                assert_never(self)

    @classmethod
    def from_separator(cls, separator: str) -> "ReviewKind":
        for kind in cls:
            if kind.separator == separator:
                return kind
        raise ValueError(f"レビューの識別子の区切りが不正です: {separator!r}")


# 作成の途中の置き場の名前の頭。本物のレビューの名前は先頭が英数字か _ なので重ならない
STAGING_PREFIX = ".creating-"
# 作成の請求のロックのファイルを置く、置き場の根の直下のディレクトリ名
CLAIMS_DIRECTORY = ".claims"
REVIEW_FILE = "review.json"


def fold_name(name: str) -> str:
    """大文字と小文字を畳んだ、名前を比べるための鍵。重なりの判定と請求の名前はこの鍵で揃える。"""
    return name.casefold()


class _SpellingTwin(NamedTuple):
    name: str
    existing: str
    parent: Path


def _folded_twin(parent: Path, name: str) -> str | None:
    """parent の下に、name と大文字と小文字だけが違う名前があればその名前を返す。

    綴りが完全に一致する名前があれば None。大文字と小文字を区別しないファイルシステムでは
    二つの綴りが同じ場所を指すので、実在の確認は存在検査でなく一覧の名前の比較で行う。
    """
    try:
        entries = [entry.name for entry in parent.iterdir()]
    except (FileNotFoundError, NotADirectoryError):
        return None
    if name in entries:
        return None
    folded = fold_name(name)
    for entry in entries:
        if fold_name(entry) == folded:
            return entry
    return None


def _spelling_twin(directory: Path) -> _SpellingTwin | None:
    """三つの部分のうち、綴り違いの既存を持つ最初の (名前, 既存の名前, 親)。"""
    for part in (directory.parent.parent, directory.parent, directory):
        twin = _folded_twin(part.parent, part.name)
        if twin is not None:
            return _SpellingTwin(part.name, twin, part.parent)
    return None


def check_new_key(kind: ReviewKind, key: str) -> None:
    """新しく作るレビューのキーの検査。作るときにだけ掛け、保存済みの識別子の解釈には掛けない。

    キーの規則に合うことに加え、保存先のディレクトリ名が PR の番号と重ならないことを求める。
    PR の番号は GitHub が振るので、名前で作る種類の側が譲る。以前に作った重なる記録は、
    保存した識別子の種類で読む。
    """
    if not kind.key_pattern.fullmatch(key):
        raise ValueError(f"{kind.label}の名前が不正です: {key!r}")
    directory_name = f"{kind.directory_prefix}{key}"
    for other in ReviewKind:
        if other is kind or not other.externally_numbered:
            continue
        if directory_name.startswith(other.directory_prefix) and other.key_pattern.fullmatch(
            directory_name.removeprefix(other.directory_prefix)
        ):
            raise ValueError(
                f"{kind.label}の名前を {key!r} にはできません"
                f"（{other.label}の番号と保存先が重なります）"
            )


def _identifier_patterns() -> tuple[re.Pattern[str], re.Pattern[str]]:
    """区切りの文字は ReviewKind の性質から集め、識別子の文法をここ以外に写さない。"""
    separators = "".join(re.escape(kind.separator) for kind in ReviewKind)
    segment = rf"[^/{separators}\s]+"
    full = re.compile(rf"^({segment})/({segment})([{separators}])({segment})$")
    short = re.compile(rf"^({segment})([{separators}])({segment})$")
    return full, short


_FULL_PATTERN, _SHORT_PATTERN = _identifier_patterns()


def full_identifier_pattern() -> re.Pattern[str]:
    """完全な識別子の文法。キーの規則は種類ごとに違い、ここには含まれない。"""
    return _FULL_PATTERN


def _kind_of(text: str, separator: str, key: str) -> ReviewKind:
    """区切りから種類を引き、その種類のキーの規則で検査する。規則は種類ごとに違い、文法の正規表現では絞れない。"""
    kind = ReviewKind.from_separator(separator)
    if not kind.key_pattern.fullmatch(key):
        raise ValueError(f"レビューの識別子の形が不正です: {text!r}")
    return kind


@dataclass(frozen=True, eq=False)
class ReviewId:
    owner: str
    repo: str
    # PR は番号、範囲とファイルのレビューと設計書は名前。ディレクトリ名として同じ位置に入る
    key: str
    kind: ReviewKind

    def _identity(self) -> tuple[str, str, str, ReviewKind]:
        return (self.owner, self.repo, self.key, self.kind)

    def __eq__(self, other: object) -> bool:
        """比べの規則はここだけに置く。検査済みの子（CheckedReviewId）も同じ規則で比べる。"""
        if isinstance(other, ReviewId):
            return self._identity() == other._identity()
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._identity())

    def __post_init__(self) -> None:
        for label, part in (("owner", self.owner), ("repo", self.repo)):
            if not COMPONENT_PATTERN.fullmatch(part):
                raise ValueError(f"レビューの識別子の {label} が不正です: {part!r}")
        if not self.kind.key_pattern.fullmatch(self.key):
            raise ValueError(f"レビューの識別子の名前または番号が不正です: {self.key!r}")

    @classmethod
    def parse(cls, text: str) -> "ReviewId":
        matched = _FULL_PATTERN.match(text)
        if matched is None:
            raise ValueError(f"レビューの識別子の形が不正です: {text!r}")
        owner, repo, separator, key = matched.groups()
        return cls(owner, repo, key, _kind_of(text, separator, key))

    @staticmethod
    def parse_short(text: str) -> tuple[str, ReviewKind, str]:
        """owner を省いた `repo<区切り>キー` を (repo, 種類, キー) に分ける。"""
        matched = _SHORT_PATTERN.match(text)
        if matched is None:
            raise ValueError(f"レビューの識別子の形が不正です: {text!r}")
        repo, separator, key = matched.groups()
        return repo, _kind_of(text, separator, key), key

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}{self.kind.separator}{self.key}"


def review_name_of_directory(directory: Path) -> str:
    """保存先 owner/repo/<接頭辞><キー> から、ReviewId.parse が読める完全な識別子の文字列を組む。

    directory_prefix の逆向きの対応。接頭辞が無いときは、数字のキーは外部が採番する種類、
    それ以外は名前で作る種類。
    データの置き場の下に無い保存先は、保存先の文字列をそのまま返す。
    """
    try:
        owner, repo, name = directory.relative_to(data_home()).parts
    except ValueError:
        return str(directory)
    # 接頭辞を持つ種類は接頭辞で決まる。持たない種類は、キーを外部が採番するか（数字か）で決まる
    for kind in ReviewKind:
        prefix = kind.directory_prefix
        if prefix and name.startswith(prefix):
            return f"{owner}/{repo}{kind.separator}{name[len(prefix) :]}"
    for kind in ReviewKind:
        if not kind.directory_prefix and kind.externally_numbered == name.isdigit():
            return f"{owner}/{repo}{kind.separator}{name}"
    return str(directory)


def _review_path(review_id: ReviewId) -> Path:
    """識別子が指す保存先。値からの計算だけで、ファイルシステムは引かない。"""
    home = data_home()
    return (
        home
        / review_id.owner
        / review_id.repo
        / f"{review_id.kind.directory_prefix}{review_id.key}"
    )


def _is_plain_part(name: str) -> bool:
    """パスの一つの部分として、解決した祖先の後ろへそのまま付けてよい名前か。

    ..、.、空の部分、区切り、ドライブや根（POSIX と Windows のどちらの形も）を含めば偽。
    付けた後の正規化で、.. が解決した祖先より上へ出る経路を作らないため
    """
    if name in ("", ".", ".."):
        return False
    for pure in (PurePosixPath, PureWindowsPath):
        parsed = pure(name)
        if parsed.anchor or len(parsed.parts) != 1:
            return False
    return True


def _resolve_existing_part(path: Path) -> Path | None:
    """存在する最も近い祖先だけを strict で解決し、まだ無い部分をそのまま後ろに付ける。

    非 strict の resolve は、Windows で途中の部分が並行して作られると、最初と二度目の失敗の
    理由が食い違い、長いパスの前置きを残した形を返す。前置きの付いた形は置き場の下と比べられない。
    まだ無い部分に _is_plain_part を満たさないもの、または宛先の無いリンクがあれば None を返す
    """
    missing: list[str] = []
    current = path
    while True:
        try:
            resolved = current.resolve(strict=True)
        except FileNotFoundError:
            if current.is_symlink() or current.is_junction():
                # 宛先の無いリンク。宛先を辿れないので比べられない。並行して作られた
                # ディレクトリはリンクでないので、まだ無い部分として後ろに付けてよい
                return None
            if current.parent == current:
                # 根まで無い（Windows の無いドライブなど）。解決できる祖先が無いので比べられない
                return None
            missing.append(current.name)
            current = current.parent
            continue
        if not all(_is_plain_part(name) for name in missing):
            return None
        return resolved.joinpath(*reversed(missing))


def _reject_outside_home(path: Path) -> None:
    """各部分の検査を通っていても、シンボリックリンクで外へ出る場合を拒む二重の守り。

    末尾は作成の競合で改名されうるので、親を解決し、末尾はリンクのときだけ解決する。
    Windows のジャンクションは is_symlink が偽を返すので、リンクとして別に見る。
    まだ無い部分を付けられない形は、比べずに外と見なす
    """
    is_link = path.is_symlink() or path.is_junction()
    if is_link:
        resolved = _resolve_existing_part(path)
    else:
        parent = _resolve_existing_part(path.parent)
        resolved = parent / path.name if parent is not None and _is_plain_part(path.name) else None
    home = _resolve_existing_part(data_home())
    if resolved is None or home is None or not resolved.is_relative_to(home):
        raise ValueError(f"レビューの置き場がデータの場所の外にあります: {path}")


class ReviewExistsError(ReviewError):
    """作ろうとしたレビューの識別子が、既にあるレビューと重なった。

    呼ぶ側は誤りとして止めず、`review_id` のレビューを開ける。
    ReviewError と同じモジュールに置くのは、保存側の作成の操作がこれを上げるため。
    文面は識別子の種類から作る。detail は理由の補足で、あれば文面の後ろに付ける。
    """

    def __init__(self, review_id: ReviewId, *, detail: str = "") -> None:
        super().__init__(f"{_existing_review_message(review_id)}{detail}")
        self.review_id = review_id


def _existing_review_message(review_id: ReviewId) -> str:
    match review_id.kind:
        case ReviewKind.PULL_REQUEST:
            return (
                f"レビューが既にあります: {review_id}。作者の修正は refresh で取り込んでください"
            )
        case ReviewKind.RANGE:
            return (
                f"レビューが既にあります: {review_id}。作り直すには refresh してください。"
                "別のレビューを残すなら --name で別の名前を付けてください"
            )
        case ReviewKind.FEATURE | ReviewKind.DESIGN_DOC:
            return f"レビューが既にあります: {review_id}"
        case _:
            assert_never(review_id.kind)


@dataclass(frozen=True, eq=False)
class CheckedReviewId(ReviewId):
    """綴りの検査を通った識別子。作れるのは検査の関数だけで、直接の生成は TypeError になる。

    保証するのは、保存先の owner、repo、ディレクトリ名に、大文字と小文字だけが違う既存の名前が
    無いこと。保存先が存在することは保証しない（作成前の識別子も同じ保証で発行する）。
    比べの規則は ReviewId のものを使い、この型は検査の印だけを足す。
    保存先がデータの場所の外へ逸脱しないことは発行の時点で確かめ、発行から使うまでの間に
    差し替えられたリンクは拒まない（データの場所は利用者のホームの下で、差し替えられる者は
    レビューの中身そのものを書き換えられる）。消す操作だけは、壊す直前に検査し直す。
    既存のレビューを読む入口の関数は、生の ReviewId でなくこの型を受ける。
    """

    # 検査で確かめた保存先。比べと hash には入れない
    _directory: Path = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        raise TypeError("CheckedReviewId は check_review_id か check_new_review_id で作ります")

    def directory(self) -> Path:
        """検査で確かめた保存先。ファイルシステムは引かない。"""
        return self._directory


class _SpellingUse(Enum):
    OPEN = "open"
    CREATE = "create"


def _check_spelling(path: Path, use: _SpellingUse) -> None:
    """保存先の綴りだけを見る。読む前と作成前の違いは、拒むときの文面だけ。"""
    twin = _spelling_twin(path)
    if twin is not None:
        match use:
            case _SpellingUse.OPEN:
                raise ReviewError(
                    f"保存先 {twin.name!r} は開きません。"
                    f"大文字と小文字だけが違う保存先 {twin.existing!r} があります"
                    f"（{twin.parent}）。保存した綴りのとおりに指定してください"
                )
            case _SpellingUse.CREATE:
                raise ReviewError(
                    f"保存先 {twin.name!r} は、大文字と小文字だけが違う既存の保存先 "
                    f"{twin.existing!r} と重なるため作れません（{twin.parent}）。"
                    "別の名前を付けてください"
                )
            case _:
                assert_never(use)


def _issue_checked(review_id: ReviewId, use: _SpellingUse) -> CheckedReviewId:
    """検査済みの識別子を発行する。外への逸脱を綴りより先に検査する。"""
    path = _review_path(review_id)
    _reject_outside_home(path)
    _check_spelling(path, use)
    issued = object.__new__(CheckedReviewId)
    for spec in fields(ReviewId):
        object.__setattr__(issued, spec.name, getattr(review_id, spec.name))
    object.__setattr__(issued, "_directory", path)
    return issued


def check_review_id(review_id: ReviewId) -> CheckedReviewId:
    """既存のレビューを開く前の検査。綴り違いの既存があれば ReviewError。"""
    return _issue_checked(review_id, _SpellingUse.OPEN)


def check_new_review_id(review_id: ReviewId) -> CheckedReviewId:
    """作成前の検査。綴り違いの既存と重なれば ReviewError。"""
    return _issue_checked(review_id, _SpellingUse.CREATE)


def open_review_directory(review_id: ReviewId) -> Path:
    """既存のレビューの保存先。綴り違いの既存があれば開かずに理由を示す。"""
    return check_review_id(review_id).directory()


def new_review_id(owner: str, repo: str, name: str, kind: ReviewKind) -> CheckedReviewId:
    """新しい識別子を、名前の規則、綴りの重なり、既存の有無の順に確かめて作る。"""
    try:
        check_new_key(kind, name)
        review_id = ReviewId(owner, repo, name, kind)
        ReviewId.parse(str(review_id))
    except ValueError as error:
        raise ReviewError(str(error)) from error
    checked = check_new_review_id(review_id)
    if (checked.directory() / REVIEW_FILE).is_file():
        raise ReviewExistsError(review_id)
    return checked
