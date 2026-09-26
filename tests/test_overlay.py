"""Laying one clip over another: placement, window, and what must not move.

Every assertion here reads decoded pixels at named coordinates rather than
averaging the frame. A whole-frame average cannot tell "a layer covers this
corner" from "the frame got slightly greener", and `tests/test_zoom.py` already
records what that costs: both zoom defects survived 175 green tests because
every test checked the plan or the argv string instead of the picture.

The fixtures are chosen so the two sources cannot be confused. `alpha` is red
and `bravo` is green, so a pixel is unambiguously base or layer. An earlier
probe for this feature used a multi-colour test pattern as the layer and a
"is it red" check to tell them apart -- the pattern contained its own red bars,
so the check silently reported base where the layer had in fact been drawn.
"""

import pytest

from tests.fixtures import ensure_clips, have_ffmpeg, pixel_at, probe_duration
from vid.lib import render
from vid.plan import Overlay, Plan
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg is required")

# The layer's box in these tests: 320x180 at (20, 20).
INSIDE = (100, 100)
OUTSIDE = (500, 300)


@pytest.fixture(scope="module")
def clips():
    return ensure_clips()


def _is_red(colour: tuple[int, int, int]) -> bool:
    return colour[0] > 150 and colour[1] < 100


def _is_green(colour: tuple[int, int, int]) -> bool:
    return colour[1] > 80 and colour[0] < 150


def test_overlay_covers_exactly_its_box(clips, tmp_path):
    """Inside the stated geometry is the layer; outside it is the base."""
    plan = Plan(source=str(clips["alpha"].path)).with_operation(
        Overlay(source=str(clips["bravo"].path), x=20, y=20, width=320, height=180)
    )
    output = render(plan, str(tmp_path / "placed.mp4"))

    inside = pixel_at(output, 1.0, *INSIDE)
    outside = pixel_at(output, 1.0, *OUTSIDE)

    assert _is_green(inside), f"inside the layer's box should be the layer, got {inside}"
    assert _is_red(outside), f"outside the layer's box should be the base, got {outside}"


def test_overlay_is_confined_to_its_window(clips, tmp_path):
    """Absent before `start`, present during, absent after `end`.

    Three timestamps, because a layer that appears and never leaves passes any
    test that only samples while it is supposed to be visible.
    """
    plan = Plan(source=str(clips["alpha"].path)).with_operation(
        Overlay(source=str(clips["bravo"].path), x=20, y=20, width=320, height=180, start=1.0, end=2.0)
    )
    output = render(plan, str(tmp_path / "windowed.mp4"))

    before = pixel_at(output, 0.4, *INSIDE)
    during = pixel_at(output, 1.5, *INSIDE)
    after = pixel_at(output, 2.6, *INSIDE)

    assert _is_red(before), f"the layer appeared before its start: {before}"
    assert _is_green(during), f"the layer is missing inside its window: {during}"
    assert _is_red(after), f"the layer outstayed its end: {after}"


def test_overlay_does_not_change_duration(clips, tmp_path):
    """Compositing changes what a frame looks like, not how many there are."""
    plain = render(Plan(source=str(clips["alpha"].path)), str(tmp_path / "plain.mp4"))
    composited = render(
        Plan(source=str(clips["alpha"].path)).with_operation(
            Overlay(source=str(clips["bravo"].path), x=20, y=20, width=320, height=180)
        ),
        str(tmp_path / "composited.mp4"),
    )

    assert probe_duration(composited) == pytest.approx(probe_duration(plain), abs=0.05), (
        "the overlay changed the edit's duration"
    )


def test_overlay_does_not_take_the_layers_sound(clips, tmp_path):
    """The layer is picture only, so the base's own tone is what survives.

    `bravo` is a fifth above `alpha`, so if the layer's audio had been taken
    the spectrum would carry it.
    """
    from tests.fixtures import tone_strength

    output = render(
        Plan(source=str(clips["alpha"].path)).with_operation(
            Overlay(source=str(clips["bravo"].path), x=20, y=20, width=320, height=180)
        ),
        str(tmp_path / "sound.mp4"),
    )

    base_tone = tone_strength(output, clips["alpha"].hz, at=1.0)
    layer_tone = tone_strength(output, clips["bravo"].hz, at=1.0)

    assert base_tone > layer_tone * 4, (
        f"the layer's tone leaked into the output: base={base_tone:.4f} layer={layer_tone:.4f}"
    )


def test_a_plan_with_an_overlay_round_trips_at_format_1(clips):
    """A new op keeps `plan_format: 1`, per contracts/plan.v1.md."""
    plan = Plan(source=str(clips["alpha"].path)).with_operation(
        Overlay(source=str(clips["bravo"].path), x=20, y=20, width=320, height=180, start=1.0, end=2.0)
    )
    restored = Plan.model_validate_json(plan.model_dump_json())

    assert restored.plan_format == 1
    assert restored.operations == plan.operations


def test_width_without_height_is_refused(clips):
    """Inventing the missing one from an unstated aspect ratio reshapes the layer."""
    from vid import lib

    with pytest.raises(VidError) as failure:
        lib.overlay(Plan(source=str(clips["alpha"].path)), str(clips["bravo"].path), width=320)

    assert "height" in str(failure.value).lower()


def test_an_end_at_or_before_the_start_is_refused(clips):
    """The layer would never be on screen, which is never what was meant."""
    from vid import lib

    with pytest.raises(VidError) as failure:
        lib.overlay(Plan(source=str(clips["alpha"].path)), str(clips["bravo"].path), start="0:02", end="0:01")

    assert "start" in str(failure.value).lower()
