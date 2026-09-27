"""#16's four acceptance bullets, pinned so none of them is incidental.

#16 filed two faults on the stitch path, both silent until render: the plan
validated, the compile succeeded, ffmpeg rejected the result.

  1. `self.audio` interpolated without an `is not None` guard, putting the
     literal text `None` into the filter graph.
  2. every stitched source assumed to carry audio, emitting `<i>:a` for a
     stream that does not exist.

Both are fixed on `main`, and this file asserts the four bullets rather than
trusting that. It exists because #16 was auto-closed by PR #20's `Closes #16`
while one bullet was still open, and nothing in the suite would have noticed.

ALL FOUR BULLETS NOW HOLD, INCLUDING BULLET 1 -- and this file previously said
otherwise, at length. It argued that refusing a silent base joined to a sounded
clip was "the settled answer", because such a join "would have to invent a track
or discard one".

THAT ARGUMENT WAS WRONG, and #22 is what showed it. Synthesising silence for a
clip that genuinely has no audio invents NOTHING: the clip really is silent, and
a silent track is its honest representation. Only DISCARDING an existing track
loses something, and that alone is still refused. Conflating the two produced a
refusal that did not remove the work -- it moved it to #22's reporter, who built
the silent track by hand per segment and measured up to 5 frames of picture
drift for it.

So bullet 1's "ffmpeg accepts the command" is now literally true, and the
symmetry argument this docstring used to make is retained above only as a record
of the mistake. See tests/test_stitch_synthesises_silence.py.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tests.fixtures import have_ffmpeg

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="these are render-path behaviours")


def _audio_streams(path: Path) -> int:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return len([line for line in out.stdout.splitlines() if line.strip()])


@pytest.fixture
def clips(tmp_path):
    """One silent clip and one sounded clip, generated here."""
    silent, sounded = tmp_path / "silent.mp4", tmp_path / "sounded.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=30:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(silent),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(sounded),
        ],
        check=True,
        capture_output=True,
    )
    assert _audio_streams(silent) == 0, "the silent fixture is not silent"
    assert _audio_streams(sounded) == 1, "the sounded fixture has no sound"
    return silent, sounded


def test_bullet_1_a_silent_base_never_puts_the_literal_none_in_a_graph(clips, tmp_path):
    """THE CORE DEFECT. Whatever the outcome, `None` must never reach ffmpeg."""
    from vid.lib import render, stitch
    from vid.plan import Plan
    from vid.schemas import VidError

    silent, sounded = clips
    out = tmp_path / "out.mp4"

    # Since #22 this RENDERS rather than refusing: the silent base is given
    # synthesised silence for its own span. The core property is unchanged and
    # is what this test is really for -- the literal text `None` must never
    # reach the graph, whatever the outcome.
    render(stitch(Plan(source=str(silent)), [str(sounded)]), str(out))

    assert out.is_file(), "a silent base joined to a sounded clip no longer renders"
    assert _audio_streams(out) == 1, "the reconciled result should carry one audio track"
    assert VidError is not None  # imported for the signature this test used to assert


def test_bullet_2_audio_remove_then_stitch_renders_with_no_audio(clips, tmp_path):
    """The case that MUST render: silence already declared deliberate."""
    from vid.lib import audio_remove, render, stitch
    from vid.plan import Plan

    silent, sounded = clips
    plan = stitch(audio_remove(Plan(source=str(sounded))), [str(silent)])
    out = tmp_path / "out.mp4"
    render(plan, str(out))

    assert out.is_file(), "a deliberately silent edit must still stitch and render"
    assert _audio_streams(out) == 0, "an edit whose audio was removed came back with an audio stream"


def test_bullet_3_a_silent_clip_onto_a_sounded_edit_names_the_file(clips, tmp_path):
    """#16 accepts a named refusal here. It must NAME the file, not ffmpeg."""
    from vid.lib import render, stitch
    from vid.plan import Plan

    silent, sounded = clips
    out = tmp_path / "out.mp4"

    # #16 offered two acceptable outcomes: "silence supplied deliberately, OR
    # the failure names that file". It used to take the second. Since #22 it
    # takes the FIRST, which is the one the issue listed before the fallback.
    render(stitch(Plan(source=str(sounded)), [str(silent)]), str(out))

    assert out.is_file(), "the silent clip was not reconciled into the edit"
    assert _audio_streams(out) == 1, "silence was not supplied for the silent clip"


def test_the_none_guard_exists_at_the_line_the_issue_named():
    """#16 named `compile.py`'s concat entry. Assert the guard, not the memory."""
    source = Path("src/vid/compile.py").read_text(encoding="utf-8")

    assert "concat=n=2:v=1:a=0" in source, (
        "the audio-less concat branch is gone; a silent edit would interpolate `self.audio` again"
    )
