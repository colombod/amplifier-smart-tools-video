"""Verbs piped together, then rendered as ONE ffmpeg pass -- the thing the
architecture is actually for, and the thing no other test file exercises.

Every existing render test proves a verb correct IN ISOLATION: build one
operation, compile it, render it, measure it. That is exactly why this bug
class survived 208 green tests. `trim` (or `cut`, `retime`, `stitch`) puts
audio alongside video because at the moment it runs that is correct; when
`audio remove` or `audio replace` follows LATER in the same plan, the audio
label the earlier verb produced is never referenced again. ffmpeg does not
prune an unconnected filter output -- it refuses the whole graph:

    Filter 'asetpts:default' has output 1 (a2) unconnected
    Error binding filtergraph inputs/outputs: Invalid argument

These tests build a real, multi-verb `Plan` through `vid.lib` (the same
functions the CLI calls) and render it through `lib.render` (the same
function `vid render` calls) -- never `compile_plan` with hand-supplied
durations, which is exactly what let this class hide: a duration passed
straight to the compiler skips `lib.render`'s own probing decision, so a gap
in THAT decision (see `test_replace_bounds_the_track_even_with_no_retime_or_
stitch_in_the_plan` below) never showed up either.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.fixtures import ensure_clips, has_audio_stream, have_ffmpeg, probe_duration, tone_strength
from vid import lib
from vid.plan import Plan

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="these render real chains")


@pytest.fixture(scope="module")
def clips():
    built = ensure_clips()
    return {name: str(clip.path) for name, clip in built.items()}


# ---------------------------------------------------------------------------
# Sibling sweep: an audio-emitting verb (trim, cut, retime, stitch) followed
# by a verb that discards the branch it produced (audio remove, audio
# replace). Every one of these raised "Invalid argument" before the fix.
# ---------------------------------------------------------------------------


def test_trim_then_remove_renders_with_the_right_duration_and_no_audio(clips, tmp_path):
    """The bug report's own reproduction, gone through the library rather than
    three piped CLI processes: trim to 2.0s, then drop the audio entirely."""
    plan = lib.trim(Plan(source=clips["alpha"]), "0.5", "2.5")
    plan = lib.audio_remove(plan)
    out = lib.render(plan, str(tmp_path / "out.mp4"))

    assert probe_duration(out) == pytest.approx(2.0, abs=0.05)
    assert not has_audio_stream(out), "audio remove left a stream behind"


def test_trim_then_replace_bounds_the_new_track_to_the_trimmed_length(clips, tmp_path):
    """Not just "does it render" -- the replacement track must be cut down to
    the TRIMMED length, not the source's, and the original tone must be gone."""
    plan = lib.trim(Plan(source=clips["alpha"]), "0.5", "2.5")
    plan = lib.audio_replace(plan, clips["bravo"])
    out = lib.render(plan, str(tmp_path / "out.mp4"))

    assert probe_duration(out) == pytest.approx(2.0, abs=0.05)
    assert tone_strength(out, 660, at=1.0) > 0.8, "the replacement track is not audible"
    assert tone_strength(out, 440, at=1.0) < 0.2, "the original track survived a replace"


def test_cut_then_remove_renders_with_the_right_duration_and_no_audio(clips, tmp_path):
    plan = lib.cut(Plan(source=clips["alpha"]), "1.0", "1.5")
    plan = lib.audio_remove(plan)
    out = lib.render(plan, str(tmp_path / "out.mp4"))

    assert probe_duration(out) == pytest.approx(2.5, abs=0.05)
    assert not has_audio_stream(out)


def test_cut_then_replace_bounds_the_new_track_to_the_cut_length(clips, tmp_path):
    plan = lib.cut(Plan(source=clips["alpha"]), "1.0", "1.5")
    plan = lib.audio_replace(plan, clips["bravo"])
    out = lib.render(plan, str(tmp_path / "out.mp4"))

    assert probe_duration(out) == pytest.approx(2.5, abs=0.05)
    assert tone_strength(out, 660, at=0.2) > 0.8
    assert tone_strength(out, 440, at=0.2) < 0.2


def test_retime_then_remove_renders_with_the_right_duration_and_no_audio(clips, tmp_path):
    plan = lib.retime(Plan(source=clips["alpha"]), speed="2x")
    plan = lib.audio_remove(plan)
    out = lib.render(plan, str(tmp_path / "out.mp4"))

    assert probe_duration(out) == pytest.approx(1.5, abs=0.1)
    assert not has_audio_stream(out)


def test_retime_ramp_then_remove_renders_with_no_audio(clips, tmp_path):
    """The ramp path builds a `concat` with its own audio branch -- a
    differently-shaped producer of the same kind of orphaned pad."""
    plan = lib.retime(Plan(source=clips["alpha"]), ramp="1x@0 1x@1.5 1x@3")
    plan = lib.audio_remove(plan)
    out = lib.render(plan, str(tmp_path / "out.mp4"))

    assert probe_duration(out) == pytest.approx(3.0, abs=0.1)
    assert not has_audio_stream(out)


def test_retime_then_replace_bounds_the_new_track(clips, tmp_path):
    plan = lib.retime(Plan(source=clips["alpha"]), speed="2x")
    plan = lib.audio_replace(plan, clips["bravo"])
    out = lib.render(plan, str(tmp_path / "out.mp4"))

    assert probe_duration(out) == pytest.approx(1.5, abs=0.1)
    assert tone_strength(out, 660, at=0.5) > 0.8
    assert tone_strength(out, 440, at=0.5) < 0.2


def test_stitch_then_remove_renders_with_the_right_duration_and_no_audio(clips, tmp_path):
    plan = lib.stitch(None, [clips["alpha"], clips["bravo"]])
    plan = lib.audio_remove(plan)
    out = lib.render(plan, str(tmp_path / "out.mp4"))

    assert probe_duration(out) == pytest.approx(6.0, abs=0.1)
    assert not has_audio_stream(out)


def test_stitch_then_replace_bounds_the_new_track_to_the_joined_length(clips, tmp_path):
    plan = lib.stitch(None, [clips["alpha"], clips["bravo"]])
    plan = lib.audio_replace(plan, clips["charlie"])
    out = lib.render(plan, str(tmp_path / "out.mp4"))

    assert probe_duration(out) == pytest.approx(6.0, abs=0.1)
    assert tone_strength(out, 880, at=0.5) > 0.8, "charlie's track is not audible"


def test_stitch_with_a_transition_then_remove_renders_with_no_audio(clips, tmp_path):
    """`_transition` builds `acrossfade` for its own audio branch -- a third,
    differently-shaped producer of the same orphaned-pad problem."""
    plan = lib.stitch(None, [clips["alpha"], clips["bravo"]], transition="fade", duration=0.5)
    plan = lib.audio_remove(plan)
    out = lib.render(plan, str(tmp_path / "out.mp4"))

    assert probe_duration(out) == pytest.approx(5.5, abs=0.1)
    assert not has_audio_stream(out)


# ---------------------------------------------------------------------------
# Controls: combinations that were ALREADY correct. Present so a fix that
# over-reaches -- sinking a pad that a later filter genuinely still needs --
# fails a test too, not just the combinations that were broken.
# ---------------------------------------------------------------------------


def test_trim_then_mix_keeps_both_tones(clips, tmp_path):
    """`audio mix` CONSUMES the branch `trim` produced (as an `amix` input),
    so it was never orphaned. Confirms the fix does not sink a pad something
    downstream still reads."""
    plan = lib.trim(Plan(source=clips["alpha"]), "0.5", "2.5")
    plan = lib.audio_mix(plan, clips["bravo"], level=-18.0)
    out = lib.render(plan, str(tmp_path / "out.mp4"))

    assert probe_duration(out) == pytest.approx(2.0, abs=0.05)
    assert tone_strength(out, 440, at=1.0) > 0.8, "the original track was lost in the mix"
    assert tone_strength(out, 660, at=1.0) > 0.0, "the mixed-in track is not present at all"


def test_remove_then_trim_still_renders(clips, tmp_path):
    """The reverse order was always fine: `audio remove` first leaves `self.audio`
    as `None` before `trim` ever runs, so there is no filter output to strand."""
    plan = lib.audio_remove(Plan(source=clips["alpha"]))
    plan = lib.trim(plan, "0.5", "2.5")
    out = lib.render(plan, str(tmp_path / "out.mp4"))

    assert probe_duration(out) == pytest.approx(2.0, abs=0.05)
    assert not has_audio_stream(out)


# ---------------------------------------------------------------------------
# The bug report's exact commands, through the real CLI and three real pipes
# -- the shape a user actually hits, not just the library underneath it.
# ---------------------------------------------------------------------------


def test_the_reported_cli_pipeline_renders_end_to_end(clips, tmp_path):
    out = tmp_path / "comp.mp4"
    trim = subprocess.run(
        [sys.executable, "-m", "vid.cli", "trim", clips["alpha"], "--from", "0:00.5", "--to", "0:02.5"],
        check=True,
        capture_output=True,
    )
    removed = subprocess.run(
        [sys.executable, "-m", "vid.cli", "audio", "remove"],
        input=trim.stdout,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "vid.cli", "render", str(out)],
        input=removed.stdout,
        check=True,
        capture_output=True,
    )

    assert probe_duration(str(out)) == pytest.approx(2.0, abs=0.05)
    assert not has_audio_stream(str(out))
