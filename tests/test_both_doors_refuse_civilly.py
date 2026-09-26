"""The same guard, reached two ways, must fail the same way.

`plan.py`'s range guards raise `ValueError` from inside Pydantic validators,
and each message is written for a caller -- naming the remedy, and whose limit
the number is. There are TWO doors onto those guards:

  - a JSON plan on stdin, where `read_plan` catches `ValidationError` itself
  - a direct verb (`vid cut --from 5 --to 2`), which builds the model by hand

Only the first was civil. The second let `ValidationError` escape `main`, so
Typer printed a Python traceback -- the carefully-worded message survived, but
buried under a type tag, the offending input dict, and a link to pydantic.dev.

Found by `check-spec-adherence` (run 2, `failure-names-remedy`), not by a test:
every existing test drove the library or the plan door, so the traceback door
was the one nothing looked through.

DRIVEN AS A SUBPROCESS, deliberately. `CliRunner.invoke(app, ...)` calls the
Typer object directly and never runs `main()`, which is where the handler
lives -- so a CliRunner test CANNOT observe this fix, passing or failing for
reasons unrelated to it. The console script is what a user runs, so that is
what these assert. Written with CliRunner first, and it could not see the
thing it was written to check.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.fixtures import ensure_clips

VID = Path(sys.executable).parent / "vid"


def run_vid(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """The console script, as a user invokes it -- `main()` included."""
    if not VID.exists():  # pragma: no cover - depends on how the venv was made
        pytest.skip(f"console script not present at {VID}")
    return subprocess.run([str(VID), *args], input=stdin, capture_output=True, text=True, timeout=120)


def test_a_direct_verb_refuses_without_a_traceback(tmp_path) -> None:
    source = ensure_clips()["alpha"].path
    result = run_vid("cut", str(source), "--from", "5", "--to", "2")

    assert result.returncode == 1, f"expected a controlled exit, got {result.returncode}"
    combined = result.stdout + result.stderr
    assert "Traceback (most recent call last)" not in combined, (
        "a Pydantic traceback reached the caller instead of the validator's own sentence"
    )
    assert "must not end before it starts" in combined, (
        f"the validator's remedy did not survive to the caller: {combined[:300]!r}"
    )
    assert "pydantic.dev" not in combined, "pydantic's developer footer reached the caller"


def test_the_plan_door_refuses_the_same_way(tmp_path) -> None:
    """The door that always worked, asserted so it keeps working."""
    source = ensure_clips()["alpha"].path
    plan = json.dumps(
        {
            "plan_format": 1,
            "source": str(source),
            "operations": [{"op": "cut", "start": 5.0, "end": 2.0}],
        }
    )
    result = run_vid("render", str(tmp_path / "out.mp4"), stdin=plan)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "Traceback (most recent call last)" not in combined
