"""sh の包みの偽の claude を起動する試験。POSIX だけの置き場に置く。

偽の claude（tests/fakes.py の write_fake_claude_wrapper）は拡張子の無い #!/bin/sh の包みで、
Windows では起動できない。置き場の違いで走る OS が決まる。
"""

import json
import sys
from pathlib import Path

import pytest
from fakes import write_fake_claude_wrapper
from test_claude import make_invocation

from jigane.llm_runs.claude import ClaudeInvocationError, FailureKind, run


def test_fake_claude_records_and_fails_as_launch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = write_fake_claude_wrapper(tmp_path)
    log = tmp_path / "fake.log"
    monkeypatch.setenv("JIGANE_CLAUDE_BINARY", str(fake))
    monkeypatch.setenv("JIGANE_FAKE_CLAUDE_LOG", str(log))
    with pytest.raises(ClaudeInvocationError) as raised:
        run(make_invocation(tmp_path))
    assert raised.value.kind == FailureKind.OTHER
    assert "終了コードが 1" in raised.value.cause
    assert "fake claude" in raised.value.cause
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert "--restricted" in entry["argv"]
    assert entry["time"]


def test_fake_claude_runs_through_a_path_containing_spaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spaced = tmp_path / "dir with 'quote' and space"
    spaced.mkdir()
    python = spaced / "python 3"
    python.symlink_to(sys.executable)
    fake = write_fake_claude_wrapper(spaced, str(python))
    log = tmp_path / "fake.log"
    monkeypatch.setenv("JIGANE_CLAUDE_BINARY", str(fake))
    monkeypatch.setenv("JIGANE_FAKE_CLAUDE_LOG", str(log))
    with pytest.raises(ClaudeInvocationError, match="fake claude"):
        run(make_invocation(tmp_path))
    assert log.read_text(encoding="utf-8").strip()
