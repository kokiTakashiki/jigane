from pathlib import Path

import pytest

from jigane.storage import store
from jigane.storage.store import ReviewId, ReviewKind, check_new_key


def test_jigane_home_takes_priority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", "/ignored")
    assert store.data_home() == tmp_path


def test_xdg_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("JIGANE_HOME", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert store.data_home() == tmp_path / "jigane"


def test_default_under_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("JIGANE_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert store.data_home() == tmp_path / ".local" / "share" / "jigane"


def test_windows_localappdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIGANE_HOME", raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", "localappdata")
    assert store.data_home() == Path("localappdata") / "jigane"


def test_parse_diff_review() -> None:
    review = ReviewId.parse("owner/repo#128")
    assert (review.owner, review.repo, review.key, review.kind) == (
        "owner",
        "repo",
        "128",
        ReviewKind.PULL_REQUEST,
    )
    assert str(review) == "owner/repo#128"


def test_parse_feature_review() -> None:
    review = ReviewId.parse("owner/repo:payment")
    assert (review.key, review.kind) == ("payment", ReviewKind.FEATURE)
    assert str(review) == "owner/repo:payment"


@pytest.mark.parametrize(
    "text", ["", "repo#1", "owner/repo", "owner/repo#x", "owner/repo:", "a/b/c#1"]
)
def test_parse_rejects_invalid(text: str) -> None:
    with pytest.raises(ValueError, match="識別子の形が不正"):
        ReviewId.parse(text)


def test_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    review = ReviewId.parse("owner/repo#128")
    assert review.directory() == tmp_path / "owner" / "repo" / "128"
    assert not review.directory().exists()


@pytest.mark.parametrize(
    "text",
    [
        "../repo#1",
        "owner/..#1",
        "./repo#1",
        "owner/.#1",
        ".hidden/repo#1",
        "owner/.hidden#1",
        "../..#1",
        "owner/repo:..",
        "owner/repo:.",
        "owner/repo:.hidden",
        "owner/repo:a\\b",
        "own\\er/repo#1",
        "-owner/repo#1",
    ],
)
def test_parse_rejects_path_components(text: str) -> None:
    with pytest.raises(ValueError, match="不正"):
        ReviewId.parse(text)


@pytest.mark.parametrize(
    ("owner", "repo", "key", "kind"),
    [
        ("..", "repo", "1", ReviewKind.PULL_REQUEST),
        ("owner", ".", "1", ReviewKind.PULL_REQUEST),
        ("owner", ".hidden", "1", ReviewKind.PULL_REQUEST),
        ("owner", "repo", "..", ReviewKind.FEATURE),
        ("owner", "repo", ".hidden", ReviewKind.FEATURE),
        ("owner", "repo", "a/b", ReviewKind.FEATURE),
        ("owner", "repo", "", ReviewKind.FEATURE),
        ("", "repo", "1", ReviewKind.PULL_REQUEST),
        ("owner/x", "repo", "1", ReviewKind.PULL_REQUEST),
        ("owner", "repo", "x", ReviewKind.PULL_REQUEST),
    ],
)
def test_constructor_rejects_unsafe_parts(
    owner: str, repo: str, key: str, kind: ReviewKind
) -> None:
    with pytest.raises(ValueError, match="不正"):
        ReviewId(owner, repo, key, kind)


@pytest.mark.parametrize("text", ["my.org/my.repo#7", "foo-bar_baz/a_b-c.d:name.v2", "o/r:x_y-z"])
def test_parse_accepts_github_names(text: str) -> None:
    assert str(ReviewId.parse(text)) == text


def test_directory_rejects_symlink_escape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    (home / "owner").mkdir(parents=True)
    outside.mkdir()
    (home / "owner" / "repo").symlink_to(outside)
    monkeypatch.setenv("JIGANE_HOME", str(home))
    with pytest.raises(ValueError, match="外"):
        ReviewId("owner", "repo", "1", ReviewKind.PULL_REQUEST).directory()


@pytest.mark.parametrize(
    ("owner", "repo", "key", "kind"),
    [
        ("owner\n", "repo", "1", ReviewKind.PULL_REQUEST),
        ("owner", "repo\n", "1", ReviewKind.PULL_REQUEST),
        ("o", "r", "1\n", ReviewKind.PULL_REQUEST),
        ("o", "r", "name\n", ReviewKind.FEATURE),
    ],
)
def test_constructor_rejects_trailing_newline(
    owner: str, repo: str, key: str, kind: ReviewKind
) -> None:
    with pytest.raises(ValueError, match="不正"):
        ReviewId(owner, repo, key, kind)


def test_parse_range_review() -> None:
    review = ReviewId.parse("owner/repo@wip")
    assert (review.owner, review.repo, review.key, review.kind) == (
        "owner",
        "repo",
        "wip",
        ReviewKind.RANGE,
    )
    assert str(review) == "owner/repo@wip"


def test_directories_of_the_three_kinds_do_not_overlap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    assert ReviewId.parse("o/r@7").directory() == tmp_path / "o" / "r" / "@7"
    assert ReviewId.parse("o/r#7").directory() == tmp_path / "o" / "r" / "7"
    assert ReviewId.parse("o/r:x").directory() == tmp_path / "o" / "r" / "x"


@pytest.mark.parametrize("text", ["o/r@", "o/r@a/b", "o/r@.x", "o/r@a@b", "o/r@a:b", "o/r#1@x"])
def test_parse_rejects_invalid_range(text: str) -> None:
    with pytest.raises(ValueError, match="不正"):
        ReviewId.parse(text)


@pytest.mark.parametrize("name", ["7", "128", "0"])
def test_digits_only_feature_name_is_rejected_only_when_creating(name: str) -> None:
    # PR の番号と保存先が同じ位置に入るので、新しくは作らせない
    with pytest.raises(ValueError, match="保存先が重なります"):
        check_new_key(ReviewKind.FEATURE, name)
    # 以前に作った記録は、識別子として構築も解釈もできる
    assert ReviewId("o", "r", name, ReviewKind.FEATURE).key == name
    assert ReviewId.parse(f"o/r:{name}") == ReviewId("o", "r", name, ReviewKind.FEATURE)


@pytest.mark.parametrize("name", ["7", "128", "0"])
def test_digits_only_range_name_is_allowed_because_of_the_at_prefix(name: str) -> None:
    check_new_key(ReviewKind.RANGE, name)


@pytest.mark.parametrize("name", ["7", "0128"])
def test_new_pull_request_number_is_not_yielded_to_itself(name: str) -> None:
    check_new_key(ReviewKind.PULL_REQUEST, name)


@pytest.mark.parametrize("kind", [ReviewKind.FEATURE, ReviewKind.RANGE])
@pytest.mark.parametrize("name", ["", "-a", "a/b", "a b"])
def test_new_key_must_fit_the_component_pattern(kind: ReviewKind, name: str) -> None:
    with pytest.raises(ValueError, match="不正"):
        check_new_key(kind, name)


@pytest.mark.parametrize("name", ["7a", "a7", "v1", "1.0", "_1"])
def test_feature_key_accepts_names_with_non_digits(name: str) -> None:
    assert ReviewId("o", "r", name, ReviewKind.FEATURE).key == name


def test_kinds_never_share_a_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    # 新しく作れるキー（check_new_key を通るもの）だけが、種類をまたいで保存先を共有しない
    keys = ["7", "7a", "v1", "main", "1.0", "@7"]
    directories: dict[Path, str] = {}
    for kind in ReviewKind:
        for key in keys:
            try:
                check_new_key(kind, key)
            except ValueError:
                # その種類では作れない組。保存先を持たせない
                continue
            review_id = ReviewId("o", "r", key, kind)
            assert review_id.directory() not in directories, (review_id, directories)
            directories[review_id.directory()] = str(review_id)
    assert len(directories) > len(keys)


# (種類, キー, 保存先のディレクトリ名)。
# 数字だけの機能は PR と同じ保存先になる。種類は保存先の名前からは引かず、
# 保存した記録の id が決める
ROUND_TRIP_TABLE = [
    (ReviewKind.PULL_REQUEST, "7", "7"),
    (ReviewKind.PULL_REQUEST, "0128", "0128"),
    (ReviewKind.RANGE, "v1", "@v1"),
    (ReviewKind.RANGE, "7", "@7"),
    (ReviewKind.RANGE, "1.0", "@1.0"),
    (ReviewKind.FEATURE, "payment", "payment"),
    (ReviewKind.FEATURE, "v1", "v1"),
    (ReviewKind.FEATURE, "7a", "7a"),
    (ReviewKind.FEATURE, "7", "7"),
    (ReviewKind.DESIGN_DOC, "shop", "+shop"),
    (ReviewKind.DESIGN_DOC, "7", "+7"),
]


@pytest.mark.parametrize(("kind", "key", "directory_name"), ROUND_TRIP_TABLE)
def test_review_id_round_trips_through_directory_and_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: ReviewKind,
    key: str,
    directory_name: str,
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    review_id = ReviewId("o", "r", key, kind)
    assert review_id.directory() == tmp_path / "o" / "r" / directory_name
    assert ReviewId.parse(str(review_id)) == review_id
    assert ReviewId.parse_short(f"r{kind.separator}{key}") == ("r", kind, key)


def test_round_trip_table_covers_every_kind() -> None:
    assert {row[0] for row in ROUND_TRIP_TABLE} == set(ReviewKind)


@pytest.mark.parametrize(
    ("kind", "externally_numbered"),
    [
        (ReviewKind.PULL_REQUEST, True),
        (ReviewKind.RANGE, False),
        (ReviewKind.FEATURE, False),
        (ReviewKind.DESIGN_DOC, False),
    ],
)
def test_kind_properties_table(kind: ReviewKind, *, externally_numbered: bool) -> None:
    assert kind.externally_numbered == externally_numbered


def test_key_overlap_check_yields_only_to_externally_numbered_kinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 重なりの規則が is_pull_request ではなく externally_numbered から引かれていることを確かめる
    monkeypatch.setattr(ReviewKind, "externally_numbered", property(lambda _self: False))
    check_new_key(ReviewKind.FEATURE, "7")


_HOSTILE_PARTS = ["..", ".", "a/b", "a/../b", "/etc", "a\x00b", "\x00", "a\\b", "", "../x", "x/.."]
_KEY_SEPARATORS = ["#", ":", "@"]


@pytest.mark.parametrize("hostile", _HOSTILE_PARTS)
@pytest.mark.parametrize("separator", _KEY_SEPARATORS)
def test_parse_rejects_hostile_parts_in_every_position(hostile: str, separator: str) -> None:
    for text in (
        f"{hostile}/repo{separator}1",
        f"owner/{hostile}{separator}1",
        f"owner/repo{separator}{hostile}",
    ):
        with pytest.raises(ValueError, match="不正"):
            ReviewId.parse(text)


@pytest.mark.parametrize("hostile", _HOSTILE_PARTS)
@pytest.mark.parametrize("kind", list(ReviewKind))
def test_constructor_rejects_hostile_parts_in_every_position(
    hostile: str, kind: ReviewKind
) -> None:
    key = "1" if kind is ReviewKind.PULL_REQUEST else "name"
    for owner, repo, candidate in (
        (hostile, "repo", key),
        ("owner", hostile, key),
        ("owner", "repo", hostile),
    ):
        with pytest.raises(ValueError, match="不正"):
            ReviewId(owner, repo, candidate, kind)


@pytest.mark.parametrize(
    "text",
    ["o/r#1", "my.org/my.repo#7", "foo-bar_baz/a_b-c.d:name.v2", "o/r:x_y-z", "o/r@7", "o/r@wip"],
)
def test_directory_stays_under_data_home(
    text: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIGANE_HOME", str(tmp_path))
    directory = ReviewId.parse(text).directory()
    assert directory.resolve().is_relative_to(tmp_path.resolve())
    assert directory != tmp_path.resolve()
