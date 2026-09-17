"""The property this design exists to have, asserted rather than assumed.

Every verb except `render` must work with no ffmpeg binary and no provider
credentials, because a plan is JSON and nothing decodes a frame until the end.

That is easy to believe and easy to break: one import of a media library at
module scope, one eager provider handshake in a constructor, and it is gone --
silently, because a developer machine has ffmpeg and credentials and would never
notice. Our research tools learned this the hard way, and their CI now asserts
the ABSENCE of six provider variables rather than trusting the environment.

So these tests run each verb in a SCRUBBED SUBPROCESS: a PATH with no ffmpeg on
it and an environment with every provider variable removed. Checking in-process
would prove nothing, because this interpreter already has both.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
VID = REPO / ".venv" / "bin" / "vid"

#: Anything that could hand a model to the tool behind our back.
PROVIDER_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "PERPLEXITY_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "COPILOT_API_KEY",
)

EMPTY_PLAN = json.dumps({"plan_format": 1, "source": "talk.mp4", "operations": []})

pytestmark = pytest.mark.skipif(not VID.exists(), reason="run `uv sync` first: no .venv/bin/vid")


def _bare_env() -> dict[str, str]:
    """A PATH with no ffmpeg and no provider credentials anywhere in it."""
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")}
    for name in PROVIDER_VARS:
        assert name not in env
    return env


def _run(args: list[str], stdin: str = EMPTY_PLAN) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(VID), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=_bare_env(),
        cwd=REPO,
    )


def test_the_scrubbed_environment_really_has_no_ffmpeg():
    """Guard the guard.

    If ffmpeg leaked onto this PATH, every test below would pass for the wrong
    reason and we would believe a property we had never tested.
    """
    probe = subprocess.run(
        [sys.executable, "-c", "import shutil,sys; sys.exit(0 if shutil.which('ffmpeg') is None else 1)"],
        env=_bare_env(),
    )
    assert probe.returncode == 0, "ffmpeg is on the scrubbed PATH -- these tests would prove nothing"


@pytest.mark.parametrize(
    "args",
    [
        ["trim", "tests/fixtures/alpha.mp4", "--from", "0:05"],
        ["cut", "tests/fixtures/alpha.mp4", "--from", "0:01", "--to", "0:02"],
        ["retime", "tests/fixtures/alpha.mp4", "--speed", "2x"],
        ["zoom", "tests/fixtures/alpha.mp4", "--to", "1.4"],
        ["stitch", "tests/fixtures/alpha.mp4", "tests/fixtures/bravo.mp4"],
        ["stitch", "tests/fixtures/alpha.mp4", "tests/fixtures/bravo.mp4", "--transition", "dissolve"],
        ["caption", "tests/fixtures/alpha.mp4", "--subtitles", "talk.srt"],
        ["plan"],
        ["check"],
        ["transitions"],
        ["manifest"],
    ],
    ids=lambda a: a[0] if isinstance(a, list) else str(a),
)
def test_every_verb_but_render_runs_with_nothing_installed(args):
    result = _run(args)
    assert result.returncode == 0, (
        f"`vid {' '.join(args)}` failed with no ffmpeg and no credentials.\nstderr: {result.stderr}"
    )


def test_print_command_needs_no_ffmpeg_either():
    """Compiling is pure; only RUNNING needs the binary.

    This is what makes the compile step testable at all -- on a machine with no
    ffmpeg, in CI, with no media on disk.
    """
    plan = json.dumps(
        {
            "plan_format": 1,
            "source": "talk.mp4",
            "operations": [{"op": "trim", "start": 10.0, "end": 20.0}],
        }
    )
    result = _run(["render", "out.mp4", "--print-command"], stdin=plan)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("ffmpeg ")
    assert "trim=start=10.0:end=20.0" in result.stdout
    assert not (REPO / "out.mp4").exists(), "--print-command wrote a file; it must only print"


def test_render_without_ffmpeg_refuses_and_names_the_remedy():
    """The one verb that DOES need it fails honestly rather than confusingly."""
    result = _run(["render", "out.mp4"], stdin=EMPTY_PLAN)
    assert result.returncode != 0
    assert "ffmpeg" in result.stderr.lower()
    assert "vid check" in result.stderr, "a refusal that does not name the remedy is half a refusal"


def test_a_description_without_a_provider_refuses_rather_than_guessing():
    """The tier-2 path degrades honestly.

    Silently falling back to `fade` would be the worst outcome: a caller who
    asked for something specific gets something generic and is never told.

    THIS TEST WAS WRONG THE FIRST TIME, in a way worth keeping. It ran the CLI in
    the "scrubbed" environment above and asserted a refusal -- but that
    environment passes HOME through, and the Copilot SDK reads gh's stored
    credentials from there. So the provider was never absent: the run SUCCEEDED,
    picking `hblur` for "soft and dreamy" with a sensible rationale.

    The failure was luckier than the pass would have been. Had the provider path
    been broken for any other reason, this test would have gone green while
    proving nothing about degradation -- a check occupying the slot where the
    real question goes.

    So it now removes the provider EXPLICITLY rather than hoping an environment
    lacks one.
    """
    from vid.schemas import VidError
    from vid.transitions import resolve

    with pytest.raises(VidError) as failure:
        resolve("soft and dreamy", intelligence=None)

    message = str(failure.value)
    assert "vid transitions" in message or "presets" in message, (
        "a refusal that does not point at the alternative is half a refusal"
    )
    assert "fade" not in message.split("presets")[0], "must not suggest a silent fallback"


def test_the_provider_scrub_is_honest_about_what_it_does_not_scrub():
    """Guard the guard, second instance.

    `_bare_env` removes provider API keys but keeps HOME, so credential stores
    under it -- gh's in particular -- remain reachable. That is fine, and it is
    recorded here so the next person does not read "scrubbed environment" and
    believe more than it means.
    """
    env = _bare_env()
    assert "HOME" in env, "several tools need HOME to run at all"
    for name in PROVIDER_VARS:
        assert name not in env
