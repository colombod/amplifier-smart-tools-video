"""The trim guard was real, tested, passing -- and dead in the shipped CLI.

`tests/test_trim_past_the_end.py` asserts that a trim starting past the end of
the material is refused, and it passed. Meanwhile `vid trim src.mp4 --from 10`
on a two-second source wrote a 261-byte MP4 with nb_streams=0 AND EXITED 0.

WHY BOTH WERE TRUE. That test calls `compile_plan` directly and HANDS IT
`durations={SOURCE: 6.0}`. The guard needs a duration to compare against, and
correctly skips itself when the length is unknown -- there is even a test
asserting it must skip, because a guard that fired on "I don't know" would
break every unprobed plan. But `render` only probed durations for operations
that were known to need them, and `Trim` was not on that list. So the real path
supplied no duration, the guard skipped, and ffmpeg was handed a seek past the
end, which it answers with an empty container and success.

A GUARD TESTED ONE LAYER ABOVE WHERE IT RUNS IS NOT TESTED. Everything about
the compile-level test was correct; it simply could not observe the thing that
was broken. These tests drive `render` end to end, so the probing decision is
inside what is under test rather than supplied by the test.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tests.fixtures import have_ffmpeg
from vid.lib import render, trim
from vid.plan import Plan
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="this is about what the render path actually does")


@pytest.fixture
def two_seconds(tmp_path):
    source = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=30:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    return source


def _streams(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=nb_streams", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    return int(out.stdout.strip() or 0)


@pytest.mark.parametrize("start", [2.0, 10.0, 1e9])
def test_a_trim_past_the_end_is_refused_by_the_render_path(two_seconds, tmp_path, start):
    """THE CASE THAT SHIPPED BROKEN. `start=10` is the one a person hits.

    1e9 is included because that is how the reviewer reached it, but the
    magnitude was never the cause -- being past the end is, and 10 on a
    two-second clip does it just as well.
    """
    out = tmp_path / "out.mp4"

    with pytest.raises(VidError) as refusal:
        render(trim(Plan(source=str(two_seconds)), start=str(start)), str(out))

    message = str(refusal.value)
    assert f"{start:g}" in message, f"the refusal does not name the start it rejected: {message!r}"
    assert "2s" in message, f"the refusal does not say how long the material is: {message!r}"
    assert not out.is_file() or _streams(out) > 0, (
        "an empty container was written anyway -- the refusal came too late to stop the file"
    )


def test_a_trim_inside_the_material_still_renders(two_seconds, tmp_path):
    """The guard must not have taken the working case with it.

    This is the half a bound-everything fix breaks, and the reason the fix is
    a probe rather than a limit on the number: 1.0 is a perfectly good trim.
    """
    out = tmp_path / "out.mp4"
    render(trim(Plan(source=str(two_seconds)), start="1"), str(out))

    assert out.is_file(), "a valid trim stopped rendering"
    assert _streams(out) == 1, f"a valid trim produced {_streams(out)} streams"
    assert out.stat().st_size > 1000, f"a valid trim produced only {out.stat().st_size} bytes"


def test_the_render_path_probes_durations_for_a_plain_trim(two_seconds, tmp_path):
    """The mechanism, pinned directly.

    The bug was not in the guard; it was that `render` never gave the guard
    anything to check. Asserting the refusal alone would pass again if someone
    bounded `Trim.start` numerically instead -- which would be wrong, because
    "past the end of THIS file" is not a property of the number.
    """
    from vid import probe as probe_module

    seen: dict[str, float] = {}
    original = probe_module.video_duration

    def recording(path: str) -> float:
        value = original(path)
        seen[path] = value
        return value

    probe_module.video_duration = recording  # ty: ignore[invalid-assignment]
    try:
        with pytest.raises(VidError):
            render(trim(Plan(source=str(two_seconds)), start="10"), str(tmp_path / "out.mp4"))
    finally:
        probe_module.video_duration = original

    assert seen, "render never probed a duration for a plain trim, so the guard had nothing to check"
    assert str(two_seconds) in seen, f"the SOURCE's duration was never probed: {list(seen)}"
    assert seen[str(two_seconds)] == pytest.approx(2.0, abs=0.1), (
        f"the probed duration is wrong, so the guard compares against nonsense: {seen}"
    )
