"""宣言と警告が利用者に見える出口の文字列と、一覧の本文の出力を守る。"""

from dataclasses import replace
from pathlib import Path

import pytest
from review_fakes import make_review

from jigane.commands import main
from jigane.commands.exit_codes import EXIT_OK
from jigane.commands.output import print_lines
from jigane.commands.views.review import declaration_lines, warning_lines
from jigane.judgment_map.declaration import Declaration, DeclarationSource, Excerpt
from jigane.reviews.lifecycle import create_feature_review
from jigane.storage.store import ReviewKind


def test_warning_lines_prefix_declaration_warnings_before_content_warnings(
    tmp_path: Path,
) -> None:
    review = make_review(tmp_path, ["a.py"], kind=ReviewKind.FEATURE)
    review = replace(
        review,
        declaration=Declaration(DeclarationSource.INTENT, "x", (), ("宣言の警告",)),
        content_warnings=["内容の警告"],
    )
    assert warning_lines(review) == ["警告: 宣言の警告", "警告: 内容の警告"]


def test_warning_lines_empty_without_warnings(tmp_path: Path) -> None:
    assert warning_lines(make_review(tmp_path, ["a.py"], kind=ReviewKind.FEATURE)) == []


def test_declaration_lines_for_intent_indents_each_line(tmp_path: Path) -> None:
    review = replace(
        make_review(tmp_path, ["a.py"], kind=ReviewKind.FEATURE),
        declaration=Declaration(DeclarationSource.INTENT, "一行目\n二行目", ()),
    )
    assert declaration_lines(review) == ["宣言（利用者の意図）:", "  一行目", "  二行目"]


def test_declaration_lines_for_excerpts_previews_five_lines_then_ellipsis(
    tmp_path: Path,
) -> None:
    text = "\n".join(f"l{n}" for n in range(1, 8))
    review = replace(
        make_review(tmp_path, ["a.py"], kind=ReviewKind.FEATURE),
        declaration=Declaration(
            DeclarationSource.EXCERPTS, None, (Excerpt("README.md", 3, 9, text),)
        ),
    )
    assert declaration_lines(review) == [
        "宣言（README と設計書の抜粋 1 件）:",
        "  README.md:3-9",
        "    l1",
        "    l2",
        "    l3",
        "    l4",
        "    l5",
        "    ...",
    ]


def test_list_body_prints_id_created_at_and_required_over_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("# a\n", encoding="utf-8")
    monkeypatch.chdir(root)
    created = create_feature_review(root, ["src"], "payment")
    assert main(["list"]) == EXIT_OK
    required = created.map_kind.to_dict()["line_counts"]["by_level"]["required"]
    total = sum(created.map_kind.to_dict()["line_counts"]["by_level"].values())
    assert capsys.readouterr().out == (
        f"{created.id}  {created.created_at}  判断が要る行 {required}/{total}\n"
    )


def test_print_lines_strips_terminal_controls_from_target_derived_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    bidi_override = chr(0x202E)
    print_lines(
        ["  F1 指摘 \x1b]52;c;ZXZpbA==\x07本文\x1b[31m赤", f"path{bidi_override}gpj.py"],
        ["通常\tの文"],
    )
    assert capsys.readouterr().out == "  F1 指摘 本文赤\npathgpj.py\n通常\tの文\n"
