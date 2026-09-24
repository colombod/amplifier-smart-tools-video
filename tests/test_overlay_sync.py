"""Picture and sound must read the SAME source time of the layer.

These tests exist because an independent review found a two-second
disagreement that this repo's own suite could not see. The cause was not
subtle code -- it was the fixtures. `alpha`, `bravo` and `charlie` are each a
constant colour carrying a constant tone, and a clip that never changes cannot
express "the picture is at 2.5s while the sound is at 0.5s". Every instant of
it looks like every other instant.

`ensure_varying_clip` changes both channels together, in step, so each moment
names a source time in the picture AND in the sound. The disagreement becomes
arithmetic.

THE CONTRACT under test: `start` is when the layer APPEARS, and it plays FROM
ITS OWN BEGINNING -- so at output time `start + d`, both the frame and the tone
come from the layer's own time `d`.
"""

import pytest

from tests.fixtures import (
    ensure_clips,
    ensure_long_base_clip,
    ensure_varying_clip,
    have_ffmpeg,
    pixel_at,
    probe_duration,
    tone_level,
)
from vid import lib
from vid.plan import Plan

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="these render real chains")

#: Each segment of the varying clip, as (colour predicate, tone).
SEGMENTS = {
    "0-1s": (lambda c: c[0] > 150, 440),
    "1-2s": (lambda c: c[1] > 80 and c[0] < 150, 660),
    "2-3s": (lambda c: c[2] > 120 and c[0] < 120, 880),
}


def _segment_seen(path, at: float) -> str:
    colour = pixel_at(path, at, 320, 180)
    for name, (looks_like, _) in SEGMENTS.items():
        if looks_like(colour):
            return name
    return f"unrecognised{colour}"


def _segment_heard(path, at: float) -> str:
    levels = {name: tone_level(path, hz, at=at - 0.1, window=0.2) for name, (_, hz) in SEGMENTS.items()}
    return max(levels, key=lambda name: levels[name])


@pytest.fixture(scope="module")
def clips():
    return ensure_clips()


@pytest.fixture(scope="module")
def base():
    """Six seconds, dark grey, 200 Hz -- disjoint from every layer segment.

    `start=2` over this leaves a FOUR-second window spanning all three layer
    segments, so the layer must be seen to ADVANCE. Over the three-second
    `alpha` the window was ONE second -- a single segment -- where a frozen
    picture and a correctly-delayed sound both classify as `0-1s` and agree by
    coincidence. That is how a shift that never survived its own rebase passed
    as a verified fix.
    """
    return ensure_long_base_clip()


@pytest.fixture(scope="module")
def varying():
    return ensure_varying_clip()


def test_the_fixture_itself_changes_in_both_channels(varying):
    """Guard the guard: if this clip ever stops varying, the tests below go
    silently blind rather than failing."""
    for at, expected in ((0.5, "0-1s"), (1.5, "1-2s"), (2.5, "2-3s")):
        assert _segment_seen(varying.path, at) == expected, f"picture at {at}s is not segment {expected}"
        assert _segment_heard(varying.path, at) == expected, f"sound at {at}s is not segment {expected}"


@pytest.mark.parametrize("policy", ["keep", "only"])
def test_a_delayed_layer_keeps_picture_and_sound_together(base, varying, tmp_path, policy):
    """Picture and sound must name the SAME layer time, at several instants.

    Sampled across three segments rather than one. A single sample cannot tell
    a layer that is advancing from one frozen on a held frame -- the frozen
    case matched at one instant and was reported as a pass.
    """
    plan = lib.overlay(Plan(source=str(base.path)), str(varying.path), 0, 0, 640, 360, start="2", audio=policy)
    out = lib.render(plan, str(tmp_path / f"delayed_{policy}.mp4"))

    for at in (2.5, 3.5, 4.5):
        seen, heard = _segment_seen(out, at), _segment_heard(out, at)
        assert seen == heard, f"at output {at}s the picture shows {seen} while the sound plays {heard}"


def test_a_delayed_layers_picture_actually_advances(base, varying, tmp_path):
    """THE TEST THE ROUND-2 REVIEW ASKED FOR.

    A frozen picture agrees with a correctly-delayed sound at exactly one
    instant. Asserting agreement alone therefore cannot separate a working
    shift from a broken one; this asserts the layer moves THROUGH its segments
    in order.
    """
    plan = lib.overlay(Plan(source=str(base.path)), str(varying.path), 0, 0, 640, 360, start="2", audio="only")
    out = lib.render(plan, str(tmp_path / "advances.mp4"))

    seen = [_segment_seen(out, at) for at in (2.5, 3.5, 4.5)]
    assert seen == ["0-1s", "1-2s", "2-3s"], f"the layer's picture did not advance; saw {seen}"

    heard = [_segment_heard(out, at) for at in (2.5, 3.5, 4.5)]
    assert heard == ["0-1s", "1-2s", "2-3s"], f"the layer's sound did not advance; heard {heard}"


def test_a_delayed_layer_plays_from_its_own_beginning(base, varying, tmp_path):
    """`start=2` reveals the layer's FIRST segment, not whichever segment its
    own clock had reached while hidden."""
    plan = lib.overlay(Plan(source=str(base.path)), str(varying.path), 0, 0, 640, 360, start="2", audio="only")
    out = lib.render(plan, str(tmp_path / "from_beginning.mp4"))

    assert _segment_seen(out, 2.5) == "0-1s", "the layer did not start from its own beginning"
    assert _segment_heard(out, 2.5) == "0-1s", "the sound did not start from the layer's own beginning"


def test_delaying_a_layer_does_not_change_the_edit_length(clips, varying, tmp_path):
    """Shifting the picture must not push the output longer."""
    plan = lib.overlay(
        Plan(source=str(clips["alpha"].path)), str(varying.path), 0, 0, 640, 360, start="2", audio="drop"
    )
    out = lib.render(plan, str(tmp_path / "length.mp4"))

    assert probe_duration(out) == pytest.approx(3.0, abs=0.15)


def test_an_undelayed_layer_is_unaffected(clips, varying, tmp_path):
    """start=0 must remain a no-op on the picture path."""
    plan = lib.overlay(Plan(source=str(clips["alpha"].path)), str(varying.path), 0, 0, 640, 360, audio="only")
    out = lib.render(plan, str(tmp_path / "undelayed.mp4"))

    for at, expected in ((0.5, "0-1s"), (1.5, "1-2s"), (2.5, "2-3s")):
        assert _segment_seen(out, at) == expected
        assert _segment_heard(out, at) == expected
