"""#22: a clip with no audio gets silence, instead of a refusal.

WHAT WAS WRONG, AND IT WAS A JUDGEMENT ERROR NOT A CODE ONE. `stitch` refused
to join a sounded edit to a silent clip, on the grounds that doing so "would
have to invent a track or discard one". That conflates two different acts:

  - SYNTHESISING SILENCE for a clip that genuinely has no audio invents
    NOTHING. The clip really is silent. No information is created or destroyed.
  - DISCARDING an existing track loses something. That is still refused.

Only the second deserved a refusal. Both got one.

THE EVIDENCE THAT SETTLED IT came from #22's reporter, who was doing this
synthesis BY HAND, per segment, because the refusal did not remove the work --
it moved it to the party with less information:

    ffmpeg -i seg.mp4 -f lavfi -t <dur> -i anullsrc=r=48000:cl=stereo \\
           -c:v copy -c:a aac -shortest seg-a.mp4

    "The silent track must be stereo 48 kHz. A mono or slightly long AAC bed
     made the picture drift by up to 5 frames and pushed the final mix's true
     peak up, because stitch has no audio bitrate/format option."

Both halves of that are why the silence here is stereo 48 kHz and why its
length comes from the probed duration rather than from `-shortest` guessing.
`test_the_synthesised_silence_does_not_shift_the_picture` is that 5-frame drift,
asserted.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tests.fixtures import have_ffmpeg

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="#22 is a render-path behaviour")


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


def _frames_and_duration(path: Path) -> tuple[int, float]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    parts = [p for p in out.stdout.replace("\n", ",").split(",") if p.strip()]
    return int(parts[0]), float(parts[1])


def _tone_rms(path: Path, start: float, length: float = 1.5) -> float:
    """RMS in a 40 Hz band around 440 Hz. True silence reads -inf."""
    out = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-ss",
            f"{start}",
            "-t",
            f"{length}",
            "-i",
            str(path),
            "-af",
            "bandpass=f=440:width_type=h:w=40,astats",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    for line in reversed(out.stderr.splitlines()):
        if "RMS level dB" in line:
            value = line.split()[-1]
            return float("-inf") if value == "-inf" else float(value)
    raise AssertionError(f"no RMS reading for {path} at {start}s")


@pytest.fixture
def clips(tmp_path):
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
    assert _audio_streams(silent) == 0
    assert _audio_streams(sounded) == 1
    return silent, sounded


def _stitch_render(first: Path, rest: Path, out: Path) -> None:
    from vid.lib import render, stitch
    from vid.plan import Plan

    render(stitch(Plan(source=str(first)), [str(rest)]), str(out))


def test_a_sounded_edit_joined_to_a_silent_clip_renders(clips, tmp_path):
    """robotdad's exact case. It used to refuse."""
    silent, sounded = clips
    out = tmp_path / "out.mp4"
    _stitch_render(sounded, silent, out)

    assert out.is_file(), "the join #22 asked for still does not render"
    assert _audio_streams(out) == 1, "the result should carry one audio track"
    _frames, duration = _frames_and_duration(out)
    assert duration == pytest.approx(4.0, abs=0.05), f"duration is {duration}, not the sum of the inputs"

    assert _tone_rms(out, 0.2) > -40, "the real audio was lost from the sounded half"
    assert _tone_rms(out, 2.2) == float("-inf"), "the silent half is not actually silent"


def test_the_reverse_order_works_too(clips, tmp_path):
    """A SILENT edit joined to a sounded clip -- the mirror case."""
    silent, sounded = clips
    out = tmp_path / "out.mp4"
    _stitch_render(silent, sounded, out)

    assert _audio_streams(out) == 1
    assert _tone_rms(out, 0.2) == float("-inf"), "the leading silence is not actually silent"
    assert _tone_rms(out, 2.2) > -40, "the real audio was lost from the sounded half"


def test_the_synthesised_silence_does_not_shift_the_picture(clips, tmp_path):
    """The 5-frame drift the reporter measured doing this by hand.

    Compared against the SAME stitch with audio already on both sides, so this
    catches a silence whose length disagrees with the clip it stands in for --
    which is exactly what `-shortest` and a slightly-long AAC bed produced.
    """
    silent, sounded = clips
    synthesised, both_sounded = tmp_path / "synth.mp4", tmp_path / "both.mp4"
    _stitch_render(sounded, silent, synthesised)
    _stitch_render(sounded, sounded, both_sounded)

    synth_frames, synth_duration = _frames_and_duration(synthesised)
    real_frames, real_duration = _frames_and_duration(both_sounded)

    assert abs(synth_frames - real_frames) <= 1, (
        f"the picture drifted: {synth_frames} frames with synthesised silence vs {real_frames} with real audio"
    )
    assert synth_duration == pytest.approx(real_duration, abs=0.05)


def test_two_silent_clips_still_produce_no_audio_stream(clips, tmp_path):
    """The video-only branch must not start acquiring silence it never had."""
    silent, _sounded = clips
    out = tmp_path / "out.mp4"
    _stitch_render(silent, silent, out)

    assert _audio_streams(out) == 0, "a wholly silent stitch grew an audio stream"


def test_audio_remove_still_wins_over_synthesis(clips, tmp_path):
    """An explicit instruction beats a helpful default.

    `audio remove` already said what to do with sound in this edit. Synthesising
    silence the caller explicitly asked not to have would be the tool overriding
    them -- the same class of error as the refusal this change removes, pointed
    the other way.
    """
    from vid.lib import audio_remove, render, stitch
    from vid.plan import Plan

    silent, sounded = clips
    out = tmp_path / "out.mp4"
    render(stitch(audio_remove(Plan(source=str(sounded))), [str(silent)]), str(out))

    assert _audio_streams(out) == 0, "`audio remove` was overridden by synthesised silence"


def test_audio_remove_wins_even_when_the_incoming_clip_has_sound(clips, tmp_path):
    """THE CASE THAT ACTUALLY EXERCISES THE GUARD. A mutation found this gap.

    `test_audio_remove_still_wins_over_synthesis` above has silence on BOTH
    sides, so `_reconcile_audio` returns at its first check -- the two sides
    already agree -- and the `audio_removed` branch is never reached. Deleting
    that branch left every test green.

    This is the mismatched case: the edit's audio was explicitly removed, and
    the incoming clip HAS sound. Without the guard the compiler would
    synthesise silence for the edit and keep the clip's track, quietly undoing
    the `audio remove` the caller asked for -- the same class of error as the
    refusal this change removes, pointed the other way.
    """
    from vid.lib import audio_remove, render, stitch
    from vid.plan import Plan

    _silent, sounded = clips
    out = tmp_path / "out.mp4"
    render(stitch(audio_remove(Plan(source=str(sounded))), [str(sounded)]), str(out))

    assert _audio_streams(out) == 0, (
        "`audio remove` was overridden: the edit acquired audio from the clip stitched onto it"
    )
