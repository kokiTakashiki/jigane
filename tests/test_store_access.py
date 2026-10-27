import pytest

from jigane.languages.base import StoreAccess, StoreFormat, StoreRole, StoreSide


def _side(role: StoreRole) -> StoreSide:
    return StoreSide(role, "f", "m.py", StoreFormat.JSON, frozenset(), frozenset(), frozenset())


def test_empty_sides_rejected() -> None:
    with pytest.raises(ValueError, match="役割の重複もない"):
        StoreAccess("m.py", frozenset(), None, ())


def test_duplicate_role_rejected() -> None:
    with pytest.raises(ValueError, match="役割の重複もない"):
        StoreAccess("m.py", frozenset(), None, (_side(StoreRole.READ), _side(StoreRole.READ)))
