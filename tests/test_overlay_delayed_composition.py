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

from tests.fixtures import ensure_long_base_clip, ensure_varying_clip, frame_row, have_ffmpeg, pixel_at, tone_level
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
    """Rightmost column still showing the layer, from ONE decoded row.

    GEOMETRY-DISCRIMINATING, which the centre-pixel assertions above are not.
    A point at (320,180) lies inside BOTH a wrongly-small 480px layer and a
    correct 640px one, so it reports the right temporal signal either way. The
    round-3 review found exactly that: the composition test passed while the
    delayed layer was 480px instead of 640px.

    Scanned with `frame_row`, not `pixel_at`. `pixel_at` spawns a process per
    pixel; its sibling's docstring already warns that scanning through it
    "turned one test file into four and a half minutes", and writing this with
    it did precisely that -- 27s to 5m18s -- before the warning was heeded.
    """
    row = frame_row(path, at, 4)
    rightmost = 0
    for x, colour in enumerate(row):
        if max(colour) > 90:
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


def _layer_area(path, at: float) -> int:
    """How many layer pixels are visible, sampled every 16th row.

    Area rather than an edge, because a circle-masked layer has no single
    meaningful right edge: the mask is inscribed by the SHORTER side, so the
    widest row moves as the layer grows. Area grows monotonically regardless.

    Every assertion on this is a RATIO or an ordering, never an absolute count,
    so the sampling stride can change without silently retuning a threshold.
    """
    return sum(sum(1 for colour in frame_row(path, at, y) if max(colour) > 90) for y in range(8, 360, 16))


def test_a_delayed_motion_still_ends_when_its_window_closes(base, varying, tmp_path):
    """start x motion x END -- the third pairing of the shape that bit us twice.

    Every defect in three review rounds lived at a COMBINATION of two features
    each individually tested: delayed start x picture timing, delayed start x
    motion geometry, base audio x edit length. This is the same shape, closed
    before someone else finds it.
    """
    plan = lib.overlay(
        Plan(source=str(base.path)),
        str(varying.path),
        0,
        0,
        160,
        90,
        start="2",
        end="4",
        audio="only",
        to_x=0,
        to_y=0,
        to_width=640,
        to_height=360,
        move_at="2",
        move_over=1.0,
    )
    out = lib.render(plan, str(tmp_path / "delayed_motion_end.mp4"))

    early, middle, done, held = (_right_edge(out, at) for at in (2.1, 2.5, 3.0, 3.5))

    assert early < middle < done, f"the layer did not grow across its window: {early} {middle} {done}"
    assert done > 600, f"the layer had not finished growing by 3.0s: right edge {done}"
    assert held > 600, f"the layer shrank after its motion finished but before --end: right edge {held}"
    assert _right_edge(out, 4.5) == 0, "the layer is still on screen after --end"


def test_a_delayed_motion_under_a_mask_grows_and_stays_masked(base, varying, tmp_path):
    """start x motion x MASK. The mask must not freeze the growth, and the
    growth must not defeat the mask.

    Asserted against an UNMASKED control rendered from the same motion. Without
    it, "the area grew" would pass just as happily with the mask silently
    dropped -- the measurement would be real and the conclusion wrong.
    """

    def build(masked: bool):
        return lib.overlay(
            Plan(source=str(base.path)),
            str(varying.path),
            0,
            0,
            160,
            90,
            start="2",
            audio="only",
            mask="circle" if masked else None,
            to_x=0,
            to_y=0,
            to_width=640,
            to_height=360,
            move_at="2",
            move_over=1.0,
        )

    out = lib.render(build(True), str(tmp_path / "delayed_motion_mask.mp4"))
    control = lib.render(build(False), str(tmp_path / "delayed_motion_nomask.mp4"))

    early, middle, done = (_layer_area(out, at) for at in (2.1, 2.5, 3.0))

    assert early < middle < done, f"the masked layer did not grow across its window: {early} {middle} {done}"

    unmasked = _layer_area(control, 3.0)
    assert done < unmasked * 0.75, (
        f"the mask was not applied to the grown layer: masked area {done} against unmasked {unmasked}. "
        "A circle inscribed by the shorter side covers roughly 44% of the rectangle."
    )
    assert done > unmasked * 0.2, f"the layer is barely visible; the mask may have swallowed it: {done}/{unmasked}"
