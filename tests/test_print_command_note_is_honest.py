"""Item 2 regression: the note must not claim omissions that did not happen."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from tests.fixtures import have_ffmpeg

REPO = Path(__file__).resolve().parents[1]
VID = str(REPO / ".venv" / "bin" / "vid")

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="drives the real CLI")


def _print(plan: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [VID, "render", "out.mp4", "--print-command"],
        input=json.dumps(plan),
        capture_output=True,
        text=True,
        cwd=REPO,
    )


def test_a_plan_that_omitted_nothing_prints_no_note() -> None:
    """`trim` supplies the length WITHOUT probing, so nothing is dropped.

    The note used to be derived from the plan's OPERATION TYPES, so this plan
    claimed the pad/trim had been omitted while the emitted argv was
    byte-identical to `render`'s. A note that names omissions which did not
    happen teaches its reader to ignore the ones that did.
    """
    result = _print(
        {
            "plan_format": 1,
            "source": "/nonexistent/video.mp4",
            "operations": [
                {"op": "trim", "start": "0", "end": "2"},
                {"op": "audio_replace", "track": "/nonexistent/track.wav"},
            ],
        }
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ffmpeg"), f"no command printed: {result.stdout!r}"
    assert "note:" not in result.stderr, (
        f"claimed an omission that never happened -- `trim` supplied the length: {result.stderr!r}"
    )


def test_a_plan_that_did_omit_something_still_says_so() -> None:
    """The other direction, so the test above cannot pass by muting all notes."""
    result = _print(
        {
            "plan_format": 1,
            "source": "/nonexistent/video.mp4",
            "operations": [{"op": "audio_replace", "track": "/nonexistent/track.wav"}],
        }
    )
    assert result.returncode == 0, result.stderr
    assert "pad and trim to the picture" in result.stderr, f"a real omission went unreported: {result.stderr!r}"


def test_a_present_but_unusable_source_is_not_called_absent(tmp_path: Path) -> None:
    """Item 4: a directory IS on the machine. Saying otherwise misdirects."""
    directory = tmp_path / "adir.mp4"
    directory.mkdir()
    # The TRACK must exist, or it is the absent path that gets reported and
    # the directory never gets a chance to be described. A genuinely missing
    # file is named first on purpose -- it is the likelier mistake.
    result = _print(
        {
            "plan_format": 1,
            "source": str(directory),
            "operations": [{"op": "audio_replace", "track": "tests/fixtures/meeting.mp4"}],
        }
    )
    assert result.returncode == 0, result.stderr
    assert "is not a readable file" in result.stderr, (
        f"a directory was described as missing from the machine: {result.stderr!r}"
    )
    assert "is not on this machine" not in result.stderr


def test_a_pure_compile_refusal_does_not_say_compile_through_vid_render() -> None:
    """Item 3: the remedy has to be actionable for whoever hit it.

    This caller IS running `vid render`. Telling them to run `vid render`
    reads like a bug in the tool.
    """
    result = _print(
        {
            "plan_format": 1,
            "source": "/nonexistent/a.mp4",
            "operations": [
                {
                    "op": "stitch",
                    "sources": ["/nonexistent/b.mp4"],
                    "transition": "fade",
                    "transition_duration": 0.5,
                }
            ],
        }
    )
    assert result.returncode != 0, "expected this plan to refuse in a pure compile"
    assert "Compile through `vid render`" not in result.stderr, (
        f"told a `vid render` caller to run `vid render`: {result.stderr!r}"
    )
    assert "drop `--print-command`" in result.stderr, f"refused without an actionable remedy: {result.stderr!r}"


def test_the_render_path_remedy_is_unchanged() -> None:
    """A library caller who compiled directly still gets the right advice."""
    from vid.compile import compile_plan
    from vid.plan import AudioReplace, Plan
    from vid.schemas import VidError

    plan = Plan(source="tests/fixtures/talk.mp4").with_operation(AudioReplace(track="tests/fixtures/meeting.mp4"))
    with pytest.raises(VidError, match="Compile through `vid render`"):
        compile_plan(plan, "out.mp4", pure_compile=False)
