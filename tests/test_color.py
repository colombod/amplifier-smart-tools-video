"""Colour transfer, checked by arithmetic rather than by looking.

The whole capability is deterministic, so the test can be too: measure the
source, measure the reference, render, and measure the result. "Did the grade
work" becomes a number -- the same move as the transition blend check.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.fixtures import have_ffmpeg
from vid.color import ColorStats, lab_to_rgb, measure_image, measure_video, rgb_to_lab, write_cube
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="these read real pixels")


def _video(path, colour: str, seconds: float = 3.0) -> str:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c={colour}:s=320x180:r=15:d={seconds},noise=alls=28:allf=t+u",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return str(path)


def _image(path, colour: str) -> str:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c={colour}:s=320x180,noise=alls=28:allf=t+u",
         "-frames:v", "1", str(path)],
        check=True, capture_output=True,
    )
    return str(path)


@pytest.mark.parametrize(
    "colour", [(1.0, 0, 0), (0, 1.0, 0), (0, 0, 1.0), (0.5, 0.5, 0.5), (1, 1, 1), (0, 0, 0)]
)
def test_the_colour_space_round_trips(colour):
    """A broken conversion fails SILENTLY -- it just grades everything wrongly."""
    back = lab_to_rgb(*rgb_to_lab(*colour))
    assert max(abs(a - b) for a, b in zip(colour, back, strict=True)) < 0.002


def test_a_render_lands_measurably_closer_to_the_reference(tmp_path):
    """The acceptance clause, and the only one that matters.

    A cool blue source, a warm orange reference. The result must sit closer to
    the reference than the source did -- measured in Lab, not judged by eye.
    """
    from vid.compile import compile_plan
    from vid.lib import recolor_op
    from vid.plan import Plan

    source = _video(tmp_path / "cool.mp4", "0x1b3a6b")
    reference = _image(tmp_path / "warm.png", "0xd98a3a")
    out = str(tmp_path / "warmed.mp4")

    plan = Plan(source=source).with_operation(recolor_op(source, reference))
    subprocess.run(compile_plan(plan, out, has_audio=False), check=True, capture_output=True)

    target = measure_image(reference)
    before = measure_video(source).distance_to(target)
    after = measure_video(out).distance_to(target)

    assert after < before, f"the grade moved AWAY from the reference: {before:.1f} -> {after:.1f}"
    assert after < before * 0.25, (
        f"only {100 * (1 - after / before):.0f}% of the gap closed; the transfer is barely working"
    )


def test_strength_lands_between_doing_nothing_and_the_full_transfer(tmp_path):
    """A dial the caller can turn, verified to actually turn."""
    from vid.compile import compile_plan
    from vid.lib import recolor_op
    from vid.plan import Plan

    source = _video(tmp_path / "cool.mp4", "0x1b3a6b")
    reference = _image(tmp_path / "warm.png", "0xd98a3a")
    target = measure_image(reference)
    start = measure_video(source).distance_to(target)

    distances = {}
    for strength in (0.3, 1.0):
        out = str(tmp_path / f"s{strength}.mp4")
        plan = Plan(source=source).with_operation(recolor_op(source, reference, strength))
        subprocess.run(compile_plan(plan, out, has_audio=False), check=True, capture_output=True)
        distances[strength] = measure_video(out).distance_to(target)

    assert distances[1.0] < distances[0.3] < start, (
        f"strength is not monotonic: full={distances[1.0]:.1f} "
        f"partial={distances[0.3]:.1f} none={start:.1f}"
    )


def test_a_flat_reference_does_not_explode(tmp_path):
    """A solid-colour reference has no spread to rescale by.

    Dividing by that near-zero standard deviation would produce a lookup table of
    infinities, and a caller pointing at a flat brand swatch is entirely normal.
    """
    source = measure_video(_video(tmp_path / "v.mp4", "0x1b3a6b"))
    flat = ColorStats(mean=(60.0, 20.0, 50.0), std=(0.0, 0.0, 0.0))
    cube = write_cube(source, flat, tmp_path / "flat.cube")

    values = [
        float(part)
        for line in cube.read_text().splitlines()
        if line and not line.startswith(("#", "LUT"))
        for part in line.split()
    ]
    assert values, "the table is empty"
    assert all(0.0 <= value <= 1.0 for value in values), "the table contains out-of-range values"


def test_strength_outside_zero_to_one_is_refused(tmp_path):
    stats = ColorStats(mean=(50.0, 0.0, 0.0), std=(20.0, 10.0, 10.0))
    with pytest.raises(VidError, match="between 0 and 1"):
        write_cube(stats, stats, tmp_path / "x.cube", strength=1.5)


def test_it_needs_no_provider(tmp_path):
    """Stated in the manifest, so it gets a test rather than a promise."""
    from vid.compile import compile_plan
    from vid.lib import recolor_op
    from vid.plan import Plan

    source = _video(tmp_path / "v.mp4", "0x1b3a6b")
    reference = _image(tmp_path / "r.png", "0xd98a3a")
    plan = Plan(source=source).with_operation(recolor_op(source, reference))
    command = compile_plan(plan, str(tmp_path / "o.mp4"), has_audio=False)
    assert command[0] == "ffmpeg"
    assert any("lut3d" in part for part in command)


def test_a_video_with_no_audio_track_still_renders(tmp_path):
    """The bug this feature tripped over, which was never about colour.

    The compiler assumed every source had a stream at `0:a`. Any video WITHOUT
    audio -- which screen recordings routinely are, and which is one of the
    commonest things this tool is pointed at -- failed at render with
    `Stream map '' matches no streams`, an ffmpeg message naming neither the file
    nor the reason. Every verb was affected, not just this one.
    """
    from vid.compile import compile_plan
    from vid.plan import Plan, Trim
    from vid.probe import has_audio

    silent = _video(tmp_path / "silent.mp4", "navy")
    assert not has_audio(silent), "the fixture was supposed to have no audio"

    out = str(tmp_path / "out.mp4")
    command = compile_plan(
        Plan(source=silent).with_operation(Trim(start=0.0, end=2.0)),
        out, has_audio=False,
    )
    assert "0:a" not in " ".join(command), "mapped an audio stream that does not exist"
    subprocess.run(command, check=True, capture_output=True)
    assert not has_audio(out)


def test_has_audio_assumes_sound_when_it_cannot_tell(tmp_path):
    """Guessing wrong in the safe direction.

    An unreadable file guessing "there is audio" degrades to the old behaviour.
    Guessing "there is none" would silently DROP sound from a file that has it,
    and losing audio is far worse than a loud failure.
    """
    from vid.probe import has_audio

    missing = tmp_path / "nope.mp4"
    assert has_audio(str(missing)) is True
