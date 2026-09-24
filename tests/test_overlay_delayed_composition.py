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
COMPOSITIONS = [
    ("plain", {}),
    ("mask", {"mask": "circle"}),
    ("opacity", {"opacity": 0.5}),
    ("motion", {"to_x": 0, "to_y": 0, "to_width": 640, "to_height": 360, "move_over": 1.0}),
]


@pytest.mark.parametrize(("label", "extra"), COMPOSITIONS, ids=[case[0] for case in COMPOSITIONS])
def test_a_delayed_layer_advances_under_every_feature(base, varying, tmp_path, label, extra):
    """Picture and sound must advance together through all three segments."""
    geometry = {"x": 160, "y": 90, "width": 320, "height": 180} if label == "motion" else {}
    plan = lib.overlay(
        Plan(source=str(base.path)),
        str(varying.path),
        geometry.get("x", 0),
        geometry.get("y", 0),
        geometry.get("width", 640),
        geometry.get("height", 360),
        start="2",
        audio="only",
        **extra,
    )
    out = lib.render(plan, str(tmp_path / f"{label}.mp4"))

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
    keyed = {"key": "colorkey", "key_colour": "0x00FF00"}

    undelayed = lib.render(
        lib.overlay(Plan(source=str(base.path)), str(varying.path), 0, 0, 640, 360, audio="only", **keyed),
        str(tmp_path / "key_undelayed.mp4"),
    )
    delayed = lib.render(
        lib.overlay(Plan(source=str(base.path)), str(varying.path), 0, 0, 640, 360, start="2", audio="only", **keyed),
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
