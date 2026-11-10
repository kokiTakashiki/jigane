import pytest

from jigane.groups.policy_runs import PolicyRole
from jigane.skills.input_budget import describe_invocation
from jigane.skills.launch_index import LaunchKind, kind_of, role_of_kind, to_index


def test_the_diagram_launch_is_stored_as_zero_whatever_the_ordinal() -> None:
    assert to_index(LaunchKind.DIAGRAM, 0) == 0
    assert to_index(LaunchKind.DIAGRAM, 5) == 0


@pytest.mark.parametrize("ordinal", [1, 2, 4])
def test_a_principle_launch_is_stored_as_its_ordinal(ordinal: int) -> None:
    assert to_index(LaunchKind.PRINCIPLE, ordinal) == ordinal


@pytest.mark.parametrize(
    ("kind", "ordinal"),
    [(LaunchKind.DIAGRAM, 0), (LaunchKind.PRINCIPLE, 1), (LaunchKind.PRINCIPLE, 3)],
)
def test_the_kind_comes_back_from_the_stored_index(kind: LaunchKind, ordinal: int) -> None:
    assert kind_of(to_index(kind, ordinal)) is kind


def test_no_index_and_a_negative_index_are_unknown() -> None:
    assert kind_of(None) is None
    assert kind_of(-1) is None


def test_the_kinds_use_the_policy_role_words() -> None:
    assert role_of_kind(LaunchKind.DIAGRAM) is PolicyRole.DIAGRAM
    assert role_of_kind(LaunchKind.PRINCIPLE) is PolicyRole.PRINCIPLE
    assert LaunchKind.DIAGRAM.value == PolicyRole.DIAGRAM.value
    assert LaunchKind.PRINCIPLE.value == PolicyRole.PRINCIPLE.value


def test_the_stop_name_of_a_broken_index_shows_the_index() -> None:
    assert describe_invocation(None, None) == "方針の図の起動"
    assert describe_invocation(None, 0) == "方針の図の起動"
    assert describe_invocation(None, 2) == "方針の原則の起動（2 本目）"
    assert describe_invocation(None, -1) == "方針の起動（番号 -1）"
