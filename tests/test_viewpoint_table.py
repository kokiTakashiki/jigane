"""観点の表（viewpoints.csv）が、いまのコードと雛形に一致することを確かめる。"""

from pathlib import Path
from string import Template

import pytest

from jigane.design import division
from jigane.groups.definitions import GroupId
from jigane.groups.viewpoints import (
    SEPARATION_OF_CONCERNS,
    VIEWPOINTS,
    ViewpointKind,
    by_number,
    design_principles,
    load_viewpoints,
    of_group,
    viewpoint_fields,
)
from jigane.skills import macro, micro, security, shared_rules
from jigane.skills.policy_principles import principle_text

HEADER = "uuid,group,kind,number,legacy_number,name,question,design_question\n"
ROW = "{uuid},macro,policy,{number},{legacy},,,\n"
UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"


def _numbers(group: GroupId) -> tuple[str, ...]:
    return tuple(viewpoint.number for viewpoint in of_group(group))


def test_numbers_match_each_group_in_code() -> None:
    asvs = tuple(f"S-ASVS-V{chapter}" for chapter in range(1, 18))
    assert _numbers(GroupId.MICRO) == micro.VIEWPOINTS
    assert _numbers(GroupId.SECURITY) == (*security.VIEWPOINTS, *asvs)
    assert _numbers(GroupId.MACRO) == macro.VIEWPOINTS


def test_rendered_prompts_list_every_question() -> None:
    templates = {
        GroupId.MICRO: "micro",
        GroupId.SECURITY: "security",
        GroupId.MACRO: "macro_diagram",
    }
    principle_lines = [principle_text(principle) for principle in design_principles()]
    checked = 0
    for viewpoint in VIEWPOINTS:
        if not viewpoint.question:
            continue
        if viewpoint.kind == ViewpointKind.DESIGN_PRINCIPLE:
            # 設計の原則は実装の群の雛形と、方針の一層目の雛形へ差し込む文の両方に載る
            line = f"- {viewpoint.number} {viewpoint.name}: {viewpoint.question}"
            assert line in principle_lines, viewpoint.number
            name = "micro"
        else:
            assert viewpoint.group is not None
            name = templates[viewpoint.group]
        rendered = Template(shared_rules.prompt_text(name)).safe_substitute(
            shared_rules.values_for(name)
        )
        head = f"{viewpoint.number} {viewpoint.name}:" if viewpoint.name else viewpoint.number
        assert f"\n- {head} {viewpoint.question}\n" in rendered, viewpoint.number
        checked += 1
    assert checked > 0


def test_security_tuple_excludes_asvs_chapters() -> None:
    assert len(security.VIEWPOINTS) == 8
    assert not any("S-ASVS" in number for number in security.VIEWPOINTS)


def test_design_prompt_lists_every_design_question() -> None:
    inserted = division.viewpoints_text()
    designed = [viewpoint for viewpoint in VIEWPOINTS if viewpoint.design_question]
    assert designed
    for viewpoint in designed:
        line = f"- {viewpoint.number} {viewpoint.name}: {viewpoint.design_question}"
        assert line in inserted.split("\n"), viewpoint.number


def _write_table(tmp_path: Path, *rows: tuple[str, str, str]) -> Path:
    table = tmp_path / "v.csv"
    body = "".join(ROW.format(uuid=u, number=n, legacy=g) for u, n, g in rows)
    table.write_text(HEADER + body, encoding="utf-8")
    return table


def test_duplicate_uuid_is_rejected(tmp_path: Path) -> None:
    table = _write_table(tmp_path, (UUID_A, "M0", "M0"), (UUID_A, "M1", "M1"))
    with pytest.raises(ValueError, match="uuid"):
        load_viewpoints(table)


def test_duplicate_number_is_rejected(tmp_path: Path) -> None:
    table = _write_table(tmp_path, (UUID_A, "M0", "A"), (UUID_B, "M0", "B"))
    with pytest.raises(ValueError, match="number"):
        load_viewpoints(table)


def test_duplicate_legacy_number_is_rejected(tmp_path: Path) -> None:
    table = _write_table(tmp_path, (UUID_A, "M0", "A"), (UUID_B, "M1", "A"))
    with pytest.raises(ValueError, match="legacy_number"):
        load_viewpoints(table)


def test_design_principles_are_listed_in_table_order() -> None:
    assert tuple(viewpoint.number for viewpoint in design_principles()) == (
        "P1",
        "P2",
        "P3",
        "P4",
    )


def test_design_principles_belong_to_no_group() -> None:
    assert [viewpoint.group for viewpoint in design_principles()] == [None] * 4


def test_of_group_macro_and_security_exclude_design_principles() -> None:
    for group in (GroupId.MACRO, GroupId.SECURITY):
        numbers = [viewpoint.number for viewpoint in of_group(group)]
        assert not [number for number in numbers if number.startswith("P")]


def test_of_group_micro_ends_with_design_principles_in_table_order() -> None:
    numbers = [viewpoint.number for viewpoint in of_group(GroupId.MICRO)]
    assert numbers[-4:] == ["P1", "P2", "P3", "P4"]
    assert numbers[:-4] == [v.number for v in VIEWPOINTS if v.group == GroupId.MICRO]
    assert numbers.index("T4") < numbers.index("P1")


def test_design_principle_uuids_are_stable() -> None:
    assert {viewpoint.number: str(viewpoint.uuid) for viewpoint in design_principles()} == {
        "P1": "622e8c84-0d44-4b94-9b9e-807506dd7048",
        "P2": "9e4f5789-d0d4-4fa1-be1b-c085fbc8e20e",
        "P3": "eb86c3ec-abd3-4830-aa1e-c7a160687071",
        "P4": "7dfa8c6a-d367-4f5b-ae32-cce643a7ec85",
    }
    assert SEPARATION_OF_CONCERNS is by_number("P4")


def test_micro_prompt_lists_design_principles_from_the_table() -> None:
    rendered = shared_rules.viewpoint_list_text(GroupId.MICRO).split("\n")
    for principle in design_principles():
        assert f"- {principle.number} {principle.name}: {principle.question}" in rendered


def test_micro_finding_model_accepts_design_principle_numbers() -> None:
    assert {"P1", "P2", "P3", "P4"} <= set(micro.VIEWPOINTS)


def test_stored_p4_viewpoint_still_resolves() -> None:
    fields = viewpoint_fields("P4")
    assert fields["viewpoint_id"] == "7dfa8c6a-d367-4f5b-ae32-cce643a7ec85"
    assert fields["viewpoint_kind"] == "design_principle"


def test_design_principle_is_found_by_number() -> None:
    assert by_number("P1").kind == ViewpointKind.DESIGN_PRINCIPLE


def test_design_principles_have_name_and_question() -> None:
    assert all(viewpoint.name and viewpoint.question for viewpoint in design_principles())
