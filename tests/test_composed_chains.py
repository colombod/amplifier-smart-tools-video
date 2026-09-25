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

from tests.fixtures import ensure_clips, has_audio_stream, have_ffmpeg, probe_duration, tone_level, tone_strength
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

    # `> 0.0` was the whole assertion here, and a mix attenuated by 40 dB --
    # inaudible -- passed it. The level was REQUESTED as -18 dB, so check that
    # it arrived at -18 dB. Measured 257.8 against a base of 2047.9, a ratio of
    # 0.126; 10**(-18/20) is 0.1259.
    base = tone_level(out, 440, at=1.0)
    mixed = tone_level(out, 660, at=1.0)
    assert base > 1000, f"the original track was lost in the mix: {base:.1f}"
    assert mixed / base == pytest.approx(0.126, rel=0.3), (
        f"the mixed-in track did not arrive at the requested -18 dB: {mixed:.1f}/{base:.1f} "
        f"= {mixed / base:.3f}, expected about 0.126"
    )


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


# ---------------------------------------------------------------------------
# Overlay in a chain. The verb is correct alone -- tests/test_overlay*.py prove
# that at length -- which is precisely the condition under which this file's
# bug class hides. An overlay that KEEPS its layer's audio emits a second audio
# branch, and a later `audio remove` discards the label that branch feeds.
# ---------------------------------------------------------------------------


def test_overlay_after_trim_is_bounded_to_the_trimmed_length(clips, tmp_path):
    """`elapsed` must be the TRIMMED length, not the source's.

    The overlay's audio is padded and trimmed back to the edit's length, so a
    stale `elapsed` here pads to the wrong target: audible as silence on the
    end, and invisible to any test that only renders an overlay alone.
    """
    plan = lib.trim(Plan(source=clips["alpha"]), "0.5", "2.5")
    plan = lib.overlay(plan, clips["bravo"], 20, 20, 320, 180, audio="keep")
    out = lib.render(plan, str(tmp_path / "trim_overlay.mp4"))

    assert probe_duration(out) == pytest.approx(2.0, abs=0.1)

    # `has_audio_stream` says a stream EXISTS, not what is in it. A stream of
    # silence, or one carrying the wrong clip, passed it.
    assert tone_level(out, 440, at=1.0) > 1000, "the trimmed base track is missing"
    assert tone_level(out, 660, at=1.0) > 1000, "the overlay's track is missing"


def test_overlay_keeping_audio_then_remove_renders_with_no_audio(clips, tmp_path):
    """The exact shape of the bug this file exists for.

    The overlay produces a mixed audio label; `audio remove` then discards it.
    If the mix were left dangling, ffmpeg would refuse the whole graph with
    "has output ... unconnected" rather than dropping the unused branch.
    """
    plan = lib.overlay(Plan(source=clips["alpha"]), clips["bravo"], 20, 20, 320, 180, audio="keep")
    plan = lib.audio_remove(plan)
    out = lib.render(plan, str(tmp_path / "overlay_remove.mp4"))

    assert probe_duration(out) == pytest.approx(3.0, abs=0.1)
    assert not has_audio_stream(out), "audio remove left the overlay's mix behind"


def test_remove_then_overlay_keeping_audio_uses_the_layer_as_the_track(clips, tmp_path):
    """The base has no audio by the time the overlay runs.

    `self.audio is None` is a normal state here, not an error, and the layer
    simply becomes the soundtrack. The guard that makes this work is the same
    one whose absence once put the literal text "None" into the graph.
    """
    plan = lib.audio_remove(Plan(source=clips["alpha"]))
    plan = lib.overlay(plan, clips["bravo"], 20, 20, 320, 180, audio="keep")
    out = lib.render(plan, str(tmp_path / "remove_overlay.mp4"))

    assert has_audio_stream(out), "the layer's sound did not become the track"
    assert probe_duration(out) == pytest.approx(3.0, abs=0.1)

    # `> 0.01` passed for a tone that is effectively gone. Both halves of the
    # claim are now measured: the layer's tone BECAME the track (1448.9), and
    # the removed base did not come back (0.1). Absent tones read 0.1-0.4 here,
    # present ones about 2047, so these thresholds sit in open space.
    layer = tone_level(out, 660, at=1.0)
    removed_base = tone_level(out, 440, at=1.0)
    assert layer > 1000, f"the layer's own tone is missing: {layer:.1f}"
    assert removed_base < 50, f"the removed base track came back: {removed_base:.1f}"


def test_overlay_after_stitch_covers_the_joined_edit(clips, tmp_path):
    """`elapsed` after a stitch is the SUM, and the overlay must respect it."""
    plan = lib.stitch(Plan(source=clips["alpha"]), [clips["bravo"]])
    plan = lib.overlay(plan, clips["charlie"], 20, 20, 320, 180, audio="keep")
    out = lib.render(plan, str(tmp_path / "stitch_overlay.mp4"))

    assert probe_duration(out) == pytest.approx(6.0, abs=0.2)

    # At t=1.0 the edit is inside alpha's segment, so bravo is correctly absent
    # (0.4) while charlie's overlay runs across the whole join (2046.2). The
    # boolean could see none of that.
    assert tone_level(out, 440, at=1.0) > 1000, "the first clip's track is missing"
    assert tone_level(out, 880, at=1.0) > 1000, "the overlay does not cover the join"


def test_overlay_after_retime_is_bounded_to_the_retimed_length(clips, tmp_path):
    """Retime changes `elapsed` by division; the overlay's pad follows it."""
    plan = lib.retime(Plan(source=clips["alpha"]), "2x")
    plan = lib.overlay(plan, clips["bravo"], 20, 20, 320, 180, audio="keep")
    out = lib.render(plan, str(tmp_path / "retime_overlay.mp4"))

    assert probe_duration(out) == pytest.approx(1.5, abs=0.15)

    # Duration alone was the entire test, on a chain whose point is that the
    # overlay's AUDIO pad follows the retimed `elapsed`. A silent or dropped
    # branch would have passed. Both tracks must survive the retime.
    assert tone_level(out, 440, at=0.7) > 1000, "the retimed base track was lost"
    assert tone_level(out, 660, at=0.7) > 1000, "the overlay's track was lost"


def test_overlay_after_cut_then_replace_still_binds_the_new_track(clips, tmp_path):
    """Three verbs, two of which touch audio, with an overlay between them."""
    plan = lib.cut(Plan(source=clips["alpha"]), "1.0", "2.0")
    plan = lib.overlay(plan, clips["bravo"], 20, 20, 320, 180)
    plan = lib.audio_replace(plan, clips["charlie"])
    out = lib.render(plan, str(tmp_path / "cut_overlay_replace.mp4"))

    assert probe_duration(out) == pytest.approx(2.0, abs=0.1)

    # The test is named "binds the NEW track" and the boolean could not see
    # WHICH track it bound. The replacement must be there AND both earlier
    # tracks gone -- measured 880 at 2046.7, with 440 and 660 at 0.3 and 0.2.
    assert tone_level(out, 880, at=1.0) > 1000, "the replacement track is missing"
    assert tone_level(out, 440, at=1.0) < 50, "the original track survived the replace"
    assert tone_level(out, 660, at=1.0) < 50, "the overlay's track survived the replace"


def test_a_masked_animated_overlay_composes_with_trim_and_audio(clips, tmp_path):
    """Everything at once: the shape, the motion, the sound and a later verb.

    Each feature has its own file proving it alone. This is the only place they
    meet, and the mask-before-resize decision is what lets them: the circle is
    merged into alpha at native size, so the scale that animates the geometry
    carries it along instead of needing a second description of the shape.
    """
    plan = lib.trim(Plan(source=clips["alpha"]), "0.0", "2.5")
    plan = lib.overlay(
        plan,
        clips["bravo"],
        320,
        0,
        160,
        90,
        mask="circle",
        mask_feather=2.0,
        to_x=0,
        to_y=0,
        to_width=640,
        to_height=360,
        move_at="0.5",
        move_over=1.0,
        audio="keep",
        audio_gain=-6.0,
        opacity=0.9,
    )
    plan = lib.audio_remove(plan)
    out = lib.render(plan, str(tmp_path / "everything.mp4"))

    assert probe_duration(out) == pytest.approx(2.5, abs=0.15)
    assert not has_audio_stream(out), "audio remove left the overlay's mix behind"


# ---------------------------------------------------------------------------
# The stitch audio guard reads STATE, and state can go stale. Compiled rather
# than rendered, because the guard's whole job is to refuse before ffmpeg runs.
# ---------------------------------------------------------------------------

_DURATIONS = {"a.mp4": 3.0, "silent.mp4": 2.0, "music.mp3": 5.0}
_SIZES = {"a.mp4": (640, 360), "silent.mp4": (640, 360)}
_SOURCE_AUDIO = {"a.mp4": True, "silent.mp4": False, "music.mp3": True}


def _compile(plan):
    from vid.compile import compile_plan

    return compile_plan(
        plan,
        "out.mp4",
        durations=_DURATIONS,
        has_audio=True,
        source_sizes=_SIZES,
        source_audio=_SOURCE_AUDIO,
    )


def test_replacing_audio_after_removing_it_restores_the_stitch_guard():
    """`audio remove -> audio replace -> stitch <silent>` must be refused.

    The guard short-circuits on an `audio_removed` flag, reading it as "the
    caller already said what to do with sound here". True right after a remove;
    FALSE once a track has been put back. Left stale, this chain walked through
    the guard and emitted a specifier for an audio stream the silent file does
    not have -- ffmpeg's own `Stream specifier ... matches no streams`, which
    names neither the file nor the reason.
    """
    from vid.schemas import VidError

    plan = lib.audio_remove(Plan(source="a.mp4"))
    plan = lib.audio_replace(plan, "music.mp3")
    plan = lib.stitch(plan, ["silent.mp4"])

    with pytest.raises(VidError) as refusal:
        _compile(plan)

    assert "silent.mp4" in str(refusal.value), "the refusal does not name the file the caller meant"


def test_removing_audio_still_permits_stitching_a_silent_clip():
    """The flag's real purpose must survive the fix.

    Dropping a clip's sound after an explicit `audio remove` is carrying out a
    stated intent, not discarding something silently. Over-correcting here
    would refuse a chain that has always been legitimate.
    """
    plan = lib.stitch(lib.audio_remove(Plan(source="a.mp4")), ["silent.mp4"])

    _compile(plan)  # must not raise


def test_a_direct_compile_without_probing_still_does_not_guess():
    """Unproven audio must not raise.

    `source_audio` is populated by the render path, which probes. A direct
    `compile_plan` has no such knowledge, and an unproven guess must not become
    a refusal -- that distinction is why my own first probe of this defect
    reported a false negative.
    """
    from vid.compile import compile_plan

    plan = lib.audio_remove(Plan(source="a.mp4"))
    plan = lib.audio_replace(plan, "music.mp3")
    plan = lib.stitch(plan, ["silent.mp4"])

    compile_plan(plan, "out.mp4", durations=_DURATIONS, has_audio=True, source_sizes=_SIZES)


# ---------------------------------------------------------------------------
# EVERY way an edit can regain sound after `audio remove`, not just the one a
# review happened to report. A previous round fixed `audio_replace` alone and
# called the guard repaired; `overlay --audio keep` and `--audio only` restore
# sound through different lines and both still walked straight through it.
# ---------------------------------------------------------------------------

#: (label, how the edit regains its sound after `audio remove`)
RESTORATIONS = [
    ("audio replace", lambda plan: lib.audio_replace(plan, "music.mp3")),
    ("overlay --audio keep", lambda plan: lib.overlay(plan, "a.mp4", 0, 0, 640, 360, audio="keep")),
    ("overlay --audio only", lambda plan: lib.overlay(plan, "a.mp4", 0, 0, 640, 360, audio="only")),
]

_FULL_DURATIONS = {"base.mp4": 3.0, "a.mp4": 3.0, "silent.mp4": 2.0, "music.mp3": 5.0}
_FULL_SIZES = {"base.mp4": (640, 360), "a.mp4": (640, 360), "silent.mp4": (640, 360)}
_FULL_AUDIO = {"base.mp4": True, "a.mp4": True, "silent.mp4": False, "music.mp3": True}


def _compile_full(plan):
    from vid.compile import compile_plan

    return compile_plan(
        plan,
        "out.mp4",
        durations=_FULL_DURATIONS,
        has_audio=True,
        source_sizes=_FULL_SIZES,
        source_audio=_FULL_AUDIO,
    )


@pytest.mark.parametrize(("label", "restore"), RESTORATIONS, ids=[case[0] for case in RESTORATIONS])
def test_every_way_of_regaining_sound_restores_the_stitch_guard(label, restore):
    """`audio remove` -> <regain sound> -> `stitch <silent>` must be refused.

    The guard short-circuits on `audio_removed`, which means "the caller
    already said what to do with sound here". True right after a remove; FALSE
    the moment any path puts a track back. Left stale, the chain emits a
    specifier for an audio stream the silent file does not have, and ffmpeg
    rejects the whole command with `Stream specifier ... matches no streams` --
    naming neither the file nor the reason.
    """
    from vid.schemas import VidError

    plan = lib.stitch(restore(lib.audio_remove(Plan(source="base.mp4"))), ["silent.mp4"])

    with pytest.raises(VidError) as refusal:
        _compile_full(plan)

    assert "silent.mp4" in str(refusal.value), f"after {label} the refusal does not name the file"


def test_regaining_sound_does_not_break_an_ordinary_stitch():
    """Guard against over-correcting in the other direction: an edit that never
    lost its sound must still stitch a sounded clip without complaint."""
    plan = lib.overlay(Plan(source="base.mp4"), "a.mp4", 0, 0, 640, 360, audio="keep")
    _compile_full(lib.stitch(plan, ["a.mp4"]))  # must not raise
