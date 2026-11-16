from jigane.groups.definitions import GroupId
from jigane.repository_docs.kinds import DocumentKind, kinds_for_groups


def test_kinds_follow_the_group_table() -> None:
    both = (DocumentKind.DEVELOPMENT, DocumentKind.SECURITY)

    assert kinds_for_groups([GroupId.MACRO]) == (DocumentKind.DEVELOPMENT,)
    assert kinds_for_groups([GroupId.MICRO]) == (DocumentKind.DEVELOPMENT,)
    assert kinds_for_groups([GroupId.SECURITY]) == both
