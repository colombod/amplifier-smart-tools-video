"""Joining clips that are not the same size.

`concat` requires matching resolution, SAR and pixel format across segments, so
mismatched sources are rejected by ffmpeg at render rather than by `vid` at plan
time. The production case that prompted this mixed 1280x720 animation with
1920x1080 recordings.

Two modes, both stated by the caller, neither a default:

    fit    preserve aspect, pad the remainder with bars. Nothing leaves frame.
    fill   preserve aspect, crop the overflow centred. Nothing is letterboxed.

Aspect is preserved in both. The tests measure a square marker in the decoded
output, because a flat colour scaled to the wrong aspect looks exactly like one
scaled to the right aspect, and a square that is still square does not.
"""

import pytest

from tests.fixtures import ensure_clips, ensure_square_clip, frame_colour, have_ffmpeg, marker_box, probe_duration
from vid.lib import render
from vid.plan import Plan, Stitch
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg is required")

BASE_WIDTH, BASE_HEIGHT = 640, 360


@pytest.fixture(scope="module")
def clips():
    return ensure_clips()


@pytest.fixture(scope="module")
def square():
    return ensure_square_clip()


def _dimensions(path) -> tuple[int, int]:
    from vid.probe import dimensions

    size = dimensions(str(path))
    assert size is not None, f"could not read dimensions of {path}"
    return size


def test_mixed_sizes_without_a_mode_is_refused_by_name(clips, square, tmp_path):
    """No silent default. Auto-normalising would change framing without saying so.

    The refusal has to carry both sizes and both modes: a caller who is told
    only "sizes differ" still does not know what to do next.
    """
    plan = Plan(source=str(clips["alpha"].path)).with_operation(Stitch(sources=[str(square.path)]))

    with pytest.raises(VidError) as failure:
        render(plan, str(tmp_path / "unstated.mp4"))

    message = str(failure.value)
    assert "640x360" in message, f"the refusal does not name the target size: {message}"
    assert "480x480" in message, f"the refusal does not name the incoming size: {message}"
    assert "fit" in message, f"the refusal does not name the `fit` mode: {message}"
    assert "fill" in message, f"the refusal does not name the `fill` mode: {message}"


def test_fit_pads_with_bars_and_keeps_the_marker_square(clips, square, tmp_path):
    """`fit` letterboxes: nothing leaves frame, so bars appear at the sides."""
    plan = Plan(source=str(clips["alpha"].path)).with_operation(Stitch(sources=[str(square.path)], fit="fit"))
    output = render(plan, str(tmp_path / "fitted.mp4"))

    assert _dimensions(output) == (BASE_WIDTH, BASE_HEIGHT)

    # Sample inside the stitched-on clip, past the base's 3 seconds.
    at = clips["alpha"].seconds + 1.0
    width, height = marker_box(output, at, BASE_WIDTH, BASE_HEIGHT)
    assert width > 0, "no marker found in the fitted segment"
    assert width == pytest.approx(height, abs=4), f"marker is {width}x{height}, so `fit` stretched it"

    # 480x480 fitted into 640x360 scales by 360/480, so a 120px marker lands at 90.
    assert width == pytest.approx(90, abs=6), f"marker is {width}px, expected about 90"


def test_fill_crops_centred_and_keeps_the_marker_square(clips, square, tmp_path):
    """`fill` crops: the frame is covered edge to edge, and edges are lost."""
    plan = Plan(source=str(clips["alpha"].path)).with_operation(Stitch(sources=[str(square.path)], fit="fill"))
    output = render(plan, str(tmp_path / "filled.mp4"))

    assert _dimensions(output) == (BASE_WIDTH, BASE_HEIGHT)

    at = clips["alpha"].seconds + 1.0
    width, height = marker_box(output, at, BASE_WIDTH, BASE_HEIGHT)
    assert width > 0, "no marker found in the filled segment"
    assert width == pytest.approx(height, abs=4), f"marker is {width}x{height}, so `fill` stretched it"

    # 480x480 filled into 640x360 scales by 640/480, so a 120px marker lands at 160.
    assert width == pytest.approx(160, abs=6), f"marker is {width}px, expected about 160"


def test_fit_and_fill_differ_in_what_reaches_the_frame_edge(clips, square, tmp_path):
    """The distinguishing test: bars under `fit`, source content under `fill`.

    Written as a comparison rather than two absolute colour assertions, because
    an assertion that holds for both modes would prove neither happened.
    """
    fitted = render(
        Plan(source=str(clips["alpha"].path)).with_operation(Stitch(sources=[str(square.path)], fit="fit")),
        str(tmp_path / "edge_fit.mp4"),
    )
    filled = render(
        Plan(source=str(clips["alpha"].path)).with_operation(Stitch(sources=[str(square.path)], fit="fill")),
        str(tmp_path / "edge_fill.mp4"),
    )

    at = clips["alpha"].seconds + 1.0
    fitted_edge = frame_colour(fitted, at)
    filled_edge = frame_colour(filled, at)

    assert fitted_edge != filled_edge, (
        f"fit and fill produced the same average frame ({fitted_edge}); one of them did not run"
    )
    # Padding is black, so the letterboxed frame is the darker of the two.
    assert sum(fitted_edge) < sum(filled_edge), (
        f"expected the padded frame to be darker: fit={fitted_edge} fill={filled_edge}"
    )


def test_normalising_does_not_change_duration(clips, square, tmp_path):
    """Geometry only. A resize that shortened the edit would be a new defect."""
    for mode in ("fit", "fill"):
        output = render(
            Plan(source=str(clips["alpha"].path)).with_operation(Stitch(sources=[str(square.path)], fit=mode)),
            str(tmp_path / f"duration_{mode}.mp4"),
        )
        assert probe_duration(output) == pytest.approx(clips["alpha"].seconds + square.seconds, abs=0.15), (
            f"`{mode}` changed the total duration"
        )


def test_same_size_sources_still_need_no_mode(clips, tmp_path):
    """The existing path is untouched: matching sizes join with nothing stated."""
    plan = Plan(source=str(clips["alpha"].path)).with_operation(Stitch(sources=[str(clips["bravo"].path)]))
    output = render(plan, str(tmp_path / "same_size.mp4"))

    assert _dimensions(output) == (BASE_WIDTH, BASE_HEIGHT)
    assert probe_duration(output) == pytest.approx(clips["alpha"].seconds + clips["bravo"].seconds, abs=0.15)
