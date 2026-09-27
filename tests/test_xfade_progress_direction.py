"""#21: xfade's P counts DOWN, and the guide said it counts up.

`EXPRESSION_GUIDE` told the model "P: 0.0 at the start to 1.0 at the end" and
taught `A*(1-P)+B*P` for a crossfade. ffmpeg computes progress as
`1 - elapsed/duration` (libavfilter/vf_xfade.c, `xfade_frame`) and passes that
straight to VAR_PROGRESS, so P is 1.0 on the first frame and 0.0 on the last --
the opposite of essentially every other progress variable.

WHY THIS SURVIVED. A reversed expression COMPILES and BLENDS. The validator
sampled only the midpoint, where a backwards transition is exactly as mixed as
a correct one, so it passed. Measured with a white A and a black B:

    A*(1-P)+B*P   ->   26, 125, 224   (starts on B -- backwards)
    A*P+B*(1-P)   ->   224, 125, 26   (starts on A -- correct)

Both blend. Only a direction check tells them apart, which is why one now runs.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tests.fixtures import have_ffmpeg
from vid.transitions import EXPRESSION_GUIDE

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="direction is measured by rendering")


@pytest.fixture
def contrasting(tmp_path):
    """White and black. Contrast is the whole point -- a fixture whose two sides
    look alike cannot show direction, and the validator correctly refuses to
    claim one in that case."""
    white, black = tmp_path / "white.mp4", tmp_path / "black.mp4"
    for path, colour in ((white, "white"), (black, "black")):
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color={colour}:s=160x120:r=30:d=2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
    return white, black


def _luma(path: Path, at: float) -> float:
    out = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-ss",
            f"{at}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    for line in out.stderr.splitlines():
        if "YAVG=" in line:
            return float(line.split("YAVG=")[1].split()[0])
    raise AssertionError(f"no YAVG for {path} at {at}s")


def test_p_actually_counts_down(contrasting, tmp_path):
    """The upstream fact, measured here rather than cited.

    `expr='P*255'` renders P itself as brightness, so the direction of P is
    directly visible in the luma.
    """
    white, black = contrasting
    out = tmp_path / "probe.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(white),
            "-i",
            str(black),
            "-filter_complex",
            "[0][1]xfade=transition=custom:duration=2:offset=0:expr='if(eq(PLANE,0),P*255,128)'",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
        capture_output=True,
    )

    early, middle, late = _luma(out, 0.1), _luma(out, 1.0), _luma(out, 1.9)

    assert early > middle > late, f"P does not count down: {early} -> {middle} -> {late}"
    assert early > 200, f"P should start near 1.0 (bright), not {early}"
    assert late < 50, f"P should end near 0.0 (dark), not {late}"


def test_the_guide_does_not_claim_p_counts_up():
    """The exact false sentence #21 reported, kept out by name."""
    assert "0.0 at the start to 1.0 at the end" not in EXPRESSION_GUIDE, (
        "the guide has gone back to claiming P counts up"
    )
    assert "counts DOWN" in EXPRESSION_GUIDE or "counting DOWN" in EXPRESSION_GUIDE, (
        "the guide no longer states the direction P actually runs"
    )


def test_every_example_in_the_guide_is_written_for_a_counting_down_p():
    """A corrected heading with uncorrected examples teaches the bug anyway.

    The examples are what a model copies, so they matter more than the prose.
    """
    assert "A*P+B*(1-P)" in EXPRESSION_GUIDE, "the corrected crossfade example is missing"
    assert "if(gt(X, W*P), B, A)" in EXPRESSION_GUIDE, "the corrected wipe example is missing"

    # The backwards forms MAY appear -- the guide shows `A*(1-P)+B*P` beside its
    # measured output precisely to say "not this one". What must never happen is
    # one appearing as a RECOMMENDATION, so every line carrying a backwards form
    # has to mark it as such. A blunt "not in" check cannot tell a warning from
    # a lesson, and failed on the guide's own counter-example.
    for backwards in ("A*(1-P)+B*P", "W*(1-P)"):
        for line in EXPRESSION_GUIDE.splitlines():
            if backwards in line:
                marked = any(word in line.lower() for word in ("wrong", "backwards", "not this"))
                assert marked, f"{backwards!r} appears unmarked, reading as guidance: {line.strip()!r}"


@pytest.mark.parametrize(
    ("label", "expression", "accepted"),
    [
        ("crossfade, correct", "A*P+B*(1-P)", True),
        ("crossfade, backwards", "A*(1-P)+B*P", False),
        ("wipe, correct", "if(gt(X,W*P),B,A)", True),
        ("wipe, backwards", "if(gt(X,W*(1-P)),B,A)", False),
    ],
    ids=lambda v: v if isinstance(v, str) else str(v),
)
def test_the_validator_rejects_a_backwards_blend(contrasting, label, expression, accepted):
    """THE GAP #21 NAMED. A backwards blend used to pass.

    Both of these compile and both blend. The midpoint check cannot separate
    them -- a reversed transition is exactly as mixed in the middle. Driving the
    REAL validator here, rather than the direction helper alone, because the gap
    was that the validator never called such a helper.
    """
    from vid.transitions import probe

    white, black = contrasting
    held, why = probe(expression, str(white), str(black), 1.0)

    assert held is accepted, f"{label}: expected {'accept' if accepted else 'reject'}, got {why!r}"
    if not accepted:
        assert "BACKWARDS" in why, f"{label}: the refusal does not say what is wrong: {why!r}"
        assert "counts DOWN" in why, f"{label}: the refusal does not name the cause: {why!r}"


def test_two_similar_clips_are_refused_rather_than_guessed(tmp_path):
    """Direction is not detectable between clips that look alike. Say so.

    Claiming a direction from two near-identical clips would be a measurement
    the pixels cannot support -- the same failure as claiming a blend between
    them, which `check_transition_at` already refuses.
    """
    from vid.verify import check_transition_direction

    out = tmp_path / "flat.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=gray:s=160x120:r=30:d=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
        capture_output=True,
    )

    result = check_transition_direction(str(out), 1.0, 2.0)

    assert not result.held
    assert "too alike" in result.note, f"the refusal does not name the reason: {result.note!r}"
