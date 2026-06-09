"""Subprocess runner streams stdout for Cloud Logging."""

from __future__ import annotations

import sys
from pathlib import Path

from tyche.ops.gcp_jobs import _run_subprocess


def test_run_subprocess_streams_stdout(tmp_path: Path, capsys) -> None:
    script = tmp_path / "echo_lines.py"
    script.write_text(
        'import sys\nfor i in range(3):\n    print(f"line-{i}", flush=True)\n',
        encoding="utf-8",
    )
    code, output = _run_subprocess([sys.executable, str(script)])
    captured = capsys.readouterr()
    assert code == 0
    assert "line-0" in captured.out
    assert "line-1" in captured.out
    assert "line-2" in captured.out
    assert output == captured.out
