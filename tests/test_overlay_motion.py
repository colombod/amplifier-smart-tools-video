"""An inset that grows to full screen, without retiming the layer.

This is the case issue #7 actually asked for. The risk it carries is not
"does it move" but "what else moved with it": the previous attempt at animated
geometry in this repo reached for `zoompan` and rendered 400 seconds from an
8-second source, because `zoompan`'s `d` is output-frames-per-input-frame.

So the tests here measure the geometry AND the things that must not have
changed: duration, frame count, frame rate, and the layer's own source timing.
"""

import subprocess

import pytest

from tests.fixtures import ensure_clips, frame_row, have_ffmpeg, probe_duration
from vid.lib import render
from vid.plan import Motion, Overlay, Plan
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg is required")

WIDTH, HEIGHT = 640, 360


@pytest.fixture(scope="module")
def clips():
    return ensure_clips()


#: The row every measurement samples. NOT the frame's middle: these insets are
#: 90px tall at y=0, so the middle row is one the layer never occupies and every
#: width reads as 0 regardless of what the animation did. Row 20 is inside the
#: inset at its smallest and still inside it at full screen.
SAMPLE_ROW = 20


def _layer_width(path, at: float) -> int:
    """How many pixels of the layer are visible across `SAMPLE_ROW`."""
    return sum(1 for colour in frame_row(path, at, SAMPLE_ROW, WIDTH) if _is_layer(colour))


def _is_layer(colour: tuple[int, int, int]) -> bool:
    return colour[1] > 80 and colour[0] < 150


def _grown(clips, tmp_path, name, **motion):
    plan = Plan(source=str(clips["alpha"].path)).with_operation(
        Overlay(
            source=str(clips["bravo"].path),
            x=320,
            y=0,
            width=160,
            height=90,
            motion=Motion(**motion),
        )
    )
    return render(plan, str(tmp_path / f"{name}.mp4"))


def test_an_inset_grows_to_full_screen(clips, tmp_path):
    """Small before the move, strictly intermediate during, full after.

    The midpoint assertion is the one that matters: a jump-cut from inset to
    full frame would satisfy "small before, big after" and is not an animation.
    """
    output = _grown(
        clips,
        tmp_path,
        "grow",
        to_x=0,
        to_y=0,
        to_width=WIDTH,
        to_height=HEIGHT,
        start=1.0,
        duration=1.0,
    )

    before = _layer_width(output, 0.5)
    middle = _layer_width(output, 1.5)
    after = _layer_width(output, 2.5)

    assert before == pytest.approx(160, abs=8), f"the inset did not start at its stated width: {before}"
    assert after == pytest.approx(WIDTH, abs=8), f"the layer did not reach full width: {after}"
    assert before < middle < after, f"no intermediate size, so this is a cut and not a move: {before} {middle} {after}"


def test_linear_easing_reaches_the_arithmetic_midpoint(clips, tmp_path):
    """Halfway through a linear move is halfway between the two sizes."""
    output = _grown(
        clips, tmp_path, "linear", to_x=0, to_y=0, to_width=WIDTH, to_height=HEIGHT, start=1.0, duration=1.0
    )

    midpoint = _layer_width(output, 1.5)
    expected = (160 + WIDTH) / 2

    assert midpoint == pytest.approx(expected, abs=20), f"linear easing landed at {midpoint}, expected about {expected}"


def test_motion_does_not_change_duration_frame_count_or_rate(clips, tmp_path):
    """The `zoompan` trap, asserted directly.

    Frame count and rate are read from the container rather than inferred from
    duration, because a render that changed both consistently would still be
    wrong and would still pass a duration-only check.
    """
    plain = render(Plan(source=str(clips["alpha"].path)), str(tmp_path / "plain_motion.mp4"))
    moved = _grown(
        clips, tmp_path, "unmoved_timing", to_x=0, to_y=0, to_width=WIDTH, to_height=HEIGHT, start=1.0, duration=1.0
    )

    def stream(path, field):
        return subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                f"stream={field}",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    assert probe_duration(moved) == pytest.approx(probe_duration(plain), abs=0.05), "motion changed the duration"
    assert stream(moved, "nb_frames") == stream(plain, "nb_frames"), "motion changed the frame count"
    assert stream(moved, "r_frame_rate") == stream(plain, "r_frame_rate"), "motion changed the frame rate"


def test_odd_target_sizes_still_render(clips, tmp_path):
    """Every animated size crosses odd values; yuv420p cannot encode one.

    Asserted by rendering to an odd target, which fails outright if the even
    rounding is missing from the emitted expression.
    """
    output = _grown(clips, tmp_path, "odd", to_x=0, to_y=0, to_width=501, to_height=283, start=0.5, duration=1.0)

    assert probe_duration(output) == pytest.approx(clips["alpha"].seconds, abs=0.15)


def test_a_move_without_a_size_keeps_the_layer_the_same_size(clips, tmp_path):
    """A pure move: `--to-x`/`--to-y` with no size target."""
    output = _grown(clips, tmp_path, "slide", to_x=0, to_y=0, start=0.5, duration=1.0)

    assert _layer_width(output, 2.5) == pytest.approx(160, abs=8), "a pure move resized the layer"


def test_one_of_to_width_to_height_is_refused(clips):
    """Same rule as --width/--height: one alone invents an aspect ratio."""
    from vid import lib

    with pytest.raises(VidError) as failure:
        lib.overlay(
            Plan(source=str(clips["alpha"].path)),
            str(clips["bravo"].path),
            width=160,
            height=90,
            to_width=640,
        )

    assert "to-height" in str(failure.value)


def test_an_unknown_easing_is_refused_by_name(clips):
    from vid import lib

    with pytest.raises(VidError) as failure:
        lib.overlay(
            Plan(source=str(clips["alpha"].path)),
            str(clips["bravo"].path),
            width=160,
            height=90,
            to_x=0,
            easing="bouncy",
        )

    message = str(failure.value)
    assert "bouncy" in message
    assert "linear" in message, "the refusal does not say what IS available"
