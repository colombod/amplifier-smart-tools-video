"""A delayed start, composed with every other overlay feature.

Two review rounds found defects in delayed-start behaviour, and both lived in a
configuration nobody had rendered. The shift itself is now correct -- but it
was changed from a timestamp adjustment to `tpad`, which PREPENDS REAL
TRANSPARENT FRAMES. Every feature that touches the layer's alpha or geometry
therefore now composes with frames that did not exist before: masks, keying,
opacity and motion all run over that padding.

This is the matrix nobody had rendered. It asserts the same contract in each
combination -- at output `start + d`, picture and sound both come from the
layer's own time `d` -- rather than only that each feature works alone.

Pixels are judged by DOMINANT CHANNEL, not by an exact colour predicate. Under
`opacity=0.5` the layer's red arrives as (148,23,22), which an exact predicate
tuned on (254,0,0) would silently fail to recognise -- a non-discriminating
predicate being precisely the defect class this file exists to catch.
"""

import pytest

from tests.fixtures import ensure_long_base_clip, ensure_varying_clip, have_ffmpeg, pixel_at, tone_level
from vid import lib
from vid.plan import Plan

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="these render real chains")

#: Layer segment -> (dominant channel, tone).
SEGMENTS = (("R", 440), ("G", 660), ("B", 880))
#: Output times at which the three segments are visible when start=2.
SAMPLES = (2.5, 3.5, 4.5)


def _dominant(pixel) -> str:
    brightest = max(pixel)
    if brightest < 60:
        return "base"
    return "R" if pixel[0] == brightest else ("G" if pixel[1] == brightest else "B")


def _heard(path, at: float) -> str:
    levels = {channel: tone_level(path, hz, at=at - 0.1, window=0.2) for channel, hz in SEGMENTS}
    return max(levels, key=lambda channel: levels[channel])


@pytest.fixture(scope="module")
def base():
    return ensure_long_base_clip()


@pytest.fixture(scope="module")
def varying():
    return ensure_varying_clip()


#: Every feature that composes with the layer's alpha or geometry.
#:
#: Each entry BUILDS its own call rather than supplying a dict to expand with
#: `**`. A heterogeneous kwargs dict cannot be narrowed by the type checker,
#: which reported one error per candidate parameter -- 30 for this one call.
#: Explicit builders keep the parametrisation and stay checkable.
COMPOSITIONS = [
    ("plain", lambda plan, src: lib.overlay(plan, src, 0, 0, 640, 360, start="2", audio="only")),
    ("mask", lambda plan, src: lib.overlay(plan, src, 0, 0, 640, 360, start="2", audio="only", mask="circle")),
    (
        "opacity",
        lambda plan, src: lib.overlay(plan, src, 0, 0, 640, 360, start="2", audio="only", opacity=0.5),
    ),
    (
        "motion",
        lambda plan, src: lib.overlay(
            plan,
            src,
            160,
            90,
            320,
            180,
            start="2",
            audio="only",
            to_x=0,
            to_y=0,
            to_width=640,
            to_height=360,
            move_over=1.0,
        ),
    ),
]


@pytest.mark.parametrize(("label", "build"), COMPOSITIONS, ids=[case[0] for case in COMPOSITIONS])
def test_a_delayed_layer_advances_under_every_feature(base, varying, tmp_path, label, build):
    """Picture and sound must advance together through all three segments."""
    out = lib.render(build(Plan(source=str(base.path)), str(varying.path)), str(tmp_path / f"{label}.mp4"))

    seen = [_dominant(pixel_at(out, at, 320, 180)) for at in SAMPLES]
    heard = [_heard(out, at) for at in SAMPLES]

    assert seen == ["R", "G", "B"], f"under {label} the layer's picture did not advance; saw {seen}"
    assert heard == ["R", "G", "B"], f"under {label} the layer's sound did not advance; heard {heard}"


def test_keying_removes_the_same_segment_delayed_or_not(base, varying, tmp_path):
    """Keying must be indifferent to the delay.

    Keying green removes the layer's middle segment, so the base shows through
    while the sound continues -- picture and sound legitimately disagree there,
    because a key affects the picture only. That is easy to misread as the
    desync this PR twice shipped, so it is pinned as a DIFFERENCE between the
    delayed and undelayed renders: the same segment must vanish in both, merely
    shifted.
    """
    # Arguments written out rather than expanded from a dict: a heterogeneous
    # kwargs mapping cannot be narrowed by the type checker, and reported one
    # error per candidate parameter.
    undelayed = lib.render(
        lib.overlay(
            Plan(source=str(base.path)),
            str(varying.path),
            0,
            0,
            640,
            360,
            audio="only",
            key="colorkey",
            key_colour="0x00FF00",
        ),
        str(tmp_path / "key_undelayed.mp4"),
    )
    delayed = lib.render(
        lib.overlay(
            Plan(source=str(base.path)),
            str(varying.path),
            0,
            0,
            640,
            360,
            start="2",
            audio="only",
            key="colorkey",
            key_colour="0x00FF00",
        ),
        str(tmp_path / "key_delayed.mp4"),
    )

    early = [_dominant(pixel_at(undelayed, at, 320, 180)) for at in (0.5, 1.5, 2.5)]
    late = [_dominant(pixel_at(delayed, at, 320, 180)) for at in SAMPLES]

    assert early == late, f"the delay changed what keying removed: {early} undelayed vs {late} delayed"
    assert early[1] == "base", f"keying green did not remove the green segment; saw {early}"


def test_keying_a_colour_the_layer_lacks_removes_nothing(base, varying, tmp_path):
    """The control for the test above.

    Without it, 'the base shows through' cannot be distinguished from 'the
    layer failed to render'. Keying magenta must leave all three segments.
    """
    out = lib.render(
        lib.overlay(
            Plan(source=str(base.path)),
            str(varying.path),
            0,
            0,
            640,
            360,
            start="2",
            audio="only",
            key="colorkey",
            key_colour="0xFF00FF",
        ),
        str(tmp_path / "key_absent.mp4"),
    )

    seen = [_dominant(pixel_at(out, at, 320, 180)) for at in SAMPLES]
    assert seen == ["R", "G", "B"], f"keying an absent colour changed the picture; saw {seen}"


def _right_edge(path, at: float) -> int:
    """Rightmost column still showing the layer, scanned along the top row.

    GEOMETRY-DISCRIMINATING, which the centre-pixel assertions above are not.
    A point at (320,180) lies inside BOTH a wrongly-small 480px layer and a
    correct 640px one, so it reports the right temporal signal either way. The
    round-3 review found exactly that: the composition test passed while the
    delayed layer was 480px instead of 640px.
    """
    rightmost = 0
    for x in range(8, 640, 8):
        if max(pixel_at(path, at, x, 4)) > 90:
            rightmost = x
    return rightmost


def test_a_delayed_motion_resizes_on_the_same_clock_it_moves_on(base, varying, tmp_path):
    """One declared motion window must not come apart into two.

    A motion's size lands in `scale=...:eval=frame` applied to the LAYER, and
    its position lands in `overlay=x=...` applied to the EDIT. `t` means layer
    time in the first and edit time in the second. While the delay was padded
    in AFTER the motion was computed, those clocks differed by exactly `start`
    and the size arrived late -- measured with start=2 and a 1s window at
    output 2-3s, the right edge sat at 152 through t=3.0 and only reached full
    width at t=5.0.

    Asserted on the EDGE rather than a centre pixel, because the centre is
    inside the layer at every size and therefore cannot see this at all.
    """
    plan = lib.overlay(
        Plan(source=str(base.path)),
        str(varying.path),
        0,
        0,
        160,
        90,
        start="2",
        audio="only",
        to_x=0,
        to_y=0,
        to_width=640,
        to_height=360,
        move_at="2",
        move_over=1.0,
    )
    out = lib.render(plan, str(tmp_path / "delayed_motion.mp4"))

    early, middle, done = (_right_edge(out, at) for at in (2.1, 2.5, 3.0))

    assert early < 260, f"the layer started too wide at 2.1s: right edge {early}"
    assert 330 < middle < 470, f"the layer was not mid-growth at 2.5s: right edge {middle}"
    assert done > 600, (
        f"the layer had not finished growing by the end of its window: right edge {done} at 3.0s. "
        "The size is running on a different clock from the position."
    )
    assert middle > early, "the layer did not grow across its motion window"
