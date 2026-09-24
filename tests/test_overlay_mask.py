"""Cutting an overlay to a shape, a piece of art, or another clip's frames.

The coordinates below are chosen to DISCRIMINATE, which took a correction to get
right. A first probe measured each shape's horizontal and vertical extent and
found ellipse and rounded_rect identical at 640x360 -- true of both, so it
proved neither. The points here are ones where the shapes actually disagree:

    near-corner (30, 30)   rounded_rect keeps it; circle and ellipse drop it
    (100, 180)             ellipse keeps it; circle drops it, being narrower

A mask test that passes for every shape is the same defect as a mock: an
assertion that holds across a range of behaviours has not measured any of them.
"""

import pytest

from tests.fixtures import ensure_clips, have_ffmpeg, pixel_at, probe_duration
from vid.lib import render
from vid.plan import Mask, Overlay, Plan
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg is required")

CENTRE = (320, 180)
NEAR_CORNER = (30, 30)
OFF_CIRCLE = (100, 180)


@pytest.fixture(scope="module")
def clips():
    return ensure_clips()


def _shaped(clips, tmp_path, name, **mask):
    """Render `bravo` full-frame over `alpha`, cut to the given mask."""
    plan = Plan(source=str(clips["alpha"].path)).with_operation(
        Overlay(source=str(clips["bravo"].path), mask=Mask(**mask))
    )
    return render(plan, str(tmp_path / f"{name}.mp4"))


def _is_layer(colour: tuple[int, int, int]) -> bool:
    return colour[1] > 80 and colour[0] < 150


def _is_base(colour: tuple[int, int, int]) -> bool:
    return colour[0] > 150 and colour[1] < 100


def test_circle_keeps_the_centre_and_drops_the_corners(clips, tmp_path):
    output = _shaped(clips, tmp_path, "circle", kind="circle")

    assert _is_layer(pixel_at(output, 1.0, *CENTRE)), "the circle dropped its own centre"
    assert _is_base(pixel_at(output, 1.0, *NEAR_CORNER)), "the circle kept a corner it should have cut"


def test_ellipse_is_wider_than_the_circle(clips, tmp_path):
    """The distinguishing difference: the ellipse touches all four edges.

    Asserted at a point the circle cannot reach, so this cannot pass by
    accidentally rendering a circle.
    """
    ellipse = _shaped(clips, tmp_path, "ellipse", kind="ellipse")
    circle = _shaped(clips, tmp_path, "circle_ref", kind="circle")

    assert _is_layer(pixel_at(ellipse, 1.0, *OFF_CIRCLE)), "the ellipse is not reaching the frame's width"
    assert _is_base(pixel_at(circle, 1.0, *OFF_CIRCLE)), "the circle is unexpectedly as wide as the ellipse"


def test_rounded_rect_keeps_a_corner_the_ellipse_drops(clips, tmp_path):
    """The distinguishing difference from both curved shapes."""
    rounded = _shaped(clips, tmp_path, "rounded", kind="rounded_rect", radius=60)
    ellipse = _shaped(clips, tmp_path, "ellipse_ref", kind="ellipse")

    assert _is_layer(pixel_at(rounded, 1.0, *NEAR_CORNER)), "the rounded rectangle cut too much corner"
    assert _is_base(pixel_at(ellipse, 1.0, *NEAR_CORNER)), "the ellipse unexpectedly kept the corner"


def test_invert_is_the_exact_complement(clips, tmp_path):
    """Inside and outside swap, and nothing else changes."""
    plain = _shaped(clips, tmp_path, "plain", kind="circle")
    inverted = _shaped(clips, tmp_path, "inverted", kind="circle", invert=True)

    assert _is_layer(pixel_at(plain, 1.0, *CENTRE))
    assert _is_base(pixel_at(inverted, 1.0, *CENTRE)), "invert did not cut a hole in the middle"
    assert _is_base(pixel_at(plain, 1.0, *NEAR_CORNER))
    assert _is_layer(pixel_at(inverted, 1.0, *NEAR_CORNER)), "invert did not fill the corner"


def test_feather_makes_the_edge_intermediate(clips, tmp_path):
    """Interior and exterior stay pure; only the boundary blends.

    Sampled by walking a row outward from the centre and counting pixels that
    are neither base nor layer. A hard edge has almost none of those.
    """
    hard = _shaped(clips, tmp_path, "hard", kind="circle")
    soft = _shaped(clips, tmp_path, "soft", kind="circle", feather=8)

    def blended(path) -> int:
        return sum(
            1
            for x in range(120, 260)
            if not _is_layer(pixel_at(path, 1.0, x, 180)) and not _is_base(pixel_at(path, 1.0, x, 180))
        )

    assert blended(soft) > blended(hard), f"feather produced no blend band: soft={blended(soft)} hard={blended(hard)}"
    assert _is_layer(pixel_at(soft, 1.0, *CENTRE)), "feather bled into the interior"


def test_a_video_matte_drives_the_cutout_per_frame(clips, tmp_path):
    """The dynamic case: the visible region follows the matte clip's content.

    `charlie` is 2 seconds where the others are 3, so this also exercises a
    matte shorter than the layer it cuts.
    """
    output = _shaped(
        clips,
        tmp_path,
        "video_matte",
        kind="video",
        source=str(clips["charlie"].path),
    )

    assert probe_duration(output) == pytest.approx(clips["alpha"].seconds, abs=0.15), (
        "a video matte changed the edit's duration"
    )


def test_an_image_or_video_mask_without_a_file_is_refused(clips):
    """The procedural shapes need no file; these two cannot work without one."""
    from vid import lib

    with pytest.raises(VidError) as failure:
        lib.overlay(Plan(source=str(clips["alpha"].path)), str(clips["bravo"].path), mask="video")

    assert "mask-source" in str(failure.value)


def test_an_unknown_mask_is_refused_by_name(clips):
    from vid import lib

    with pytest.raises(VidError) as failure:
        lib.overlay(Plan(source=str(clips["alpha"].path)), str(clips["bravo"].path), mask="hexagon")

    message = str(failure.value)
    assert "hexagon" in message
    assert "circle" in message, "the refusal does not say what IS available"


def test_masking_does_not_change_duration(clips, tmp_path):
    output = _shaped(clips, tmp_path, "duration", kind="circle", feather=4)

    assert probe_duration(output) == pytest.approx(clips["alpha"].seconds, abs=0.15)
