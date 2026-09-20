"""`zoom`, rendered and measured -- not just checked for plan or argv shape.

Every one of these bugs survived 175 green unit tests because every existing
test checked the PLAN or the compiled ARGV STRING. Both were fine: the plan
carried the right numbers and the filter graph parsed. The defects were only
visible in the RENDERED file -- a duration 50x too long, and an `--at` window
with nothing gating it -- so these tests render a real clip through
`compile_plan` and measure the result with ffprobe, the same way
`test_generated_transitions.py` and `test_audio.py` already do for their own
capabilities.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.fixtures import corner_pixel, ensure_clips, ensure_corner_clip, have_ffmpeg, probe_duration
from vid.compile import compile_plan
from vid.plan import Plan, Zoom

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="these render real frames")


def render(plan: Plan, out: str) -> str:
    subprocess.run(compile_plan(plan, out), check=True, capture_output=True)
    return out


def frame_size(path: str) -> tuple[int, int]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            path,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    width, height = out.split(",")
    return int(width), int(height)


@pytest.fixture(scope="module")
def clip():
    return ensure_clips()["alpha"]


def test_zoom_preserves_the_source_duration(clip, tmp_path):
    """The bug this guards: `--to 1.3 --at 0:02 --duration 2` on an 8s/25fps
    source measured 400s out -- `zoompan`'s `d` is output frames PER INPUT
    FRAME CONSUMED, not the effect's length, and `d` was set to
    `duration * fps`. One frame at 25fps stretched into 60.
    """
    out = render(
        Plan(source=str(clip.path)).with_operation(Zoom(to=1.3, at=1.0, duration=1.0)),
        str(tmp_path / "zoomed.mp4"),
    )
    assert probe_duration(out) == pytest.approx(clip.seconds, abs=1 / 30), (
        f"zoom changed the clip's duration: {probe_duration(out)}s from a {clip.seconds}s source"
    )


def test_zoom_preserves_the_source_resolution(clip, tmp_path):
    """A second, independent defect in the same filter graph: with no `s=`
    on `zoompan`, its output silently defaulted to 1280x720 regardless of
    the source, and the final `scale=iw/4:-2` only made that worse (320x180
    out of a 640x360 source, measured directly).
    """
    out = render(
        Plan(source=str(clip.path)).with_operation(Zoom(to=1.3, at=1.0, duration=1.0)),
        str(tmp_path / "zoomed.mp4"),
    )
    assert frame_size(out) == frame_size(str(clip.path))


def test_zoom_render_is_not_absurdly_slow(clip, tmp_path):
    """A regression guard on the FIX, not just the bug: a compile that still
    produced a wildly-long clip would also make this test slow. Bounding wall
    time here means a future regression fails fast instead of hanging CI.
    """
    import time

    started = time.monotonic()
    render(
        Plan(source=str(clip.path)).with_operation(Zoom(to=1.3, at=1.0, duration=1.0)),
        str(tmp_path / "zoomed.mp4"),
    )
    assert time.monotonic() - started < 30, "rendering a 3s clip took over 30s -- likely ballooned again"


@pytest.fixture(scope="module")
def corner():
    return ensure_corner_clip()


def test_zoom_is_confined_to_its_at_and_duration_window(corner, tmp_path):
    """`--at`/`--duration` name a WINDOW: hold, ramp, hold. The bug this
    guards left nothing gating the ramp at all, so the zoom applied across
    the whole clip regardless of `--at`.

    The corner fixture's marker sits at (0,0); the default `x`/`y` crop
    toward centre, so a real zoom pushes it out of frame. Comparing the
    zoomed render's OWN corner, before and after the window, to the
    UN-ZOOMED render at the same timestamps is what makes this a test of
    confinement rather than of whether a zoom happened at all.
    """
    zoomed = render(
        Plan(source=str(corner.path)).with_operation(Zoom(to=1.8, at=1.0, duration=1.0)),
        str(tmp_path / "corner_zoomed.mp4"),
    )
    plain = render(Plan(source=str(corner.path)), str(tmp_path / "corner_plain.mp4"))

    before = corner_pixel(zoomed, 0.2)
    after = corner_pixel(zoomed, 2.5)
    reference_before = corner_pixel(plain, 0.2)
    reference_after = corner_pixel(plain, 2.5)

    assert before == reference_before, (
        f"the corner marker moved before `--at` even started: {before} vs unzoomed {reference_before}"
    )
    assert after != reference_after, (
        "the corner marker is still there well after the zoom window ends -- "
        f"zoomed {after} should differ from the un-zoomed {reference_after} once held at `--to`"
    )
    # Red background, blue marker: a crop that pushed the marker out of frame
    # reads as LESS blue at the corner than the un-zoomed reference did.
    assert after[2] < reference_after[2] - 40, f"corner blue channel barely moved: {after} vs {reference_after}"
