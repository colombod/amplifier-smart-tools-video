"""`vid mask` delivers a matte you can decode, and these tests decode it.

WHAT THE VERB IS FOR. Issue #17 asks for a matte a caller can "inspect, reuse,
version, or derive from existing media" -- the cases the inline `overlay --mask`
field cannot cover. So every assertion here reads PIXELS out of a written file.
An exit code would prove the command ran; it would not prove a matte exists, is
single-channel, or tracks anything. That distinction is the whole point of the
verb, and `tests/test_zoom.py` records what it cost to learn: both zoom defects
survived 175 green tests because every test checked the plan or the argv string
rather than the picture.

THE CONTRACT BEING DEMONSTRATED
  - one canonical format: ffv1 in .mkv, single-channel `gray`, at the caller's
    dimensions and frame rate
  - white keeps, black drops
  - `wipe` opens over its duration
  - `from-video` tracks the keyed subject, per frame
  - what it writes, `vid overlay --mask video --mask-source` reads
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tests.fixtures import ensure_moving_subject_clip, have_ffmpeg
from vid.lib import MATTE_CODEC, MATTE_PIX_FMT, mask_from_video, mask_wipe
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="a matte is a rendered file, so this needs ffmpeg")


def _stream(path: Path) -> list[str]:
    """codec, width, height, pix_fmt, rate -- read back from the written file."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,r_frame_rate",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip().split(",")


def _mean_luma(path: Path, at: float, crop: str | None = None) -> float:
    """Mean luma of one decoded frame, optionally of one crop of it.

    On a matte this IS the revealed fraction scaled to 0..255, because the only
    values present are black and white.
    """
    chain = f"{crop}," if crop else ""
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
            f"{chain}signalstats,metadata=print:key=lavfi.signalstats.YAVG",
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
    raise AssertionError(f"no YAVG for {path} at {at}s; ffmpeg said: {out.stderr[-200:]}")


# ---------------------------------------------------------------------------
# The format contract, read back rather than requested.
# ---------------------------------------------------------------------------


def test_a_wipe_is_written_in_the_one_canonical_format(tmp_path) -> None:
    """MEASURED, not assumed -- this is exactly how the first version was wrong.

    `-pix_fmt gray` was passed as an output option and trusted. The file came
    back `yuvj420p` WITH EXIT CODE 0, because mp4/h264 normalises it away, and a
    docstring promised a guarantee that did not hold. Reading the format back is
    what caught it, so reading it back is what this test does.
    """
    out = tmp_path / "reveal.mkv"
    mask_wipe("linear", str(out), width=320, height=180, seconds=2.0, fps=30.0)

    codec, width, height, pix_fmt, rate = _stream(out)

    assert codec == MATTE_CODEC, f"a matte must be {MATTE_CODEC}, not {codec}"
    assert pix_fmt == MATTE_PIX_FMT, f"a matte must be single-channel {MATTE_PIX_FMT}, not {pix_fmt}"
    assert (int(width), int(height)) == (320, 180), "the matte is not the size that was asked for"
    assert rate == "30/1", f"the matte is not the frame rate that was asked for: {rate}"


def test_an_output_that_cannot_hold_the_format_is_refused(tmp_path) -> None:
    """Refusing beats writing a matte that quietly is not one."""
    with pytest.raises(VidError) as failure:
        mask_wipe("linear", str(tmp_path / "reveal.mp4"))

    message = str(failure.value)
    assert ".mkv" in message, message
    assert "yuvj420p" in message, f"the refusal does not carry the measurement that justifies it: {message!r}"


# ---------------------------------------------------------------------------
# `wipe`: it opens. Asserted on the picture, at three points in time.
# ---------------------------------------------------------------------------


def test_a_wipe_actually_opens_over_its_duration(tmp_path) -> None:
    """A static matte and an animating one are only distinguishable by time."""
    out = tmp_path / "reveal.mkv"
    mask_wipe("linear", str(out), width=320, height=180, seconds=2.0, fps=30.0, direction="left")

    start, middle, end = (_mean_luma(out, t) for t in (0.05, 1.0, 1.95))

    assert start < middle < end, f"the wipe does not open: {start} -> {middle} -> {end}"
    assert start < 30, f"a wipe should begin essentially closed, not at {start}"
    assert end > 225, f"a wipe should finish essentially open, not at {end}"


def test_every_wipe_shape_opens(tmp_path) -> None:
    """radial and barn_door are separate expressions; neither is linear's alias."""
    for shape in ("linear", "radial", "barn_door"):
        out = tmp_path / f"{shape}.mkv"
        mask_wipe(shape, str(out), width=320, height=180, seconds=2.0, fps=30.0)

        start, end = _mean_luma(out, 0.05), _mean_luma(out, 1.95)
        assert end > start + 100, f"{shape} did not open: {start} -> {end}"


def test_an_unknown_shape_is_refused_naming_the_real_ones(tmp_path) -> None:
    with pytest.raises(VidError) as failure:
        mask_wipe("spiral", str(tmp_path / "x.mkv"))

    message = str(failure.value)
    assert "spiral" in message, message
    assert "linear" in message, message
    assert "barn_door" in message, message


# ---------------------------------------------------------------------------
# `from-video`: the white region TRACKS the subject. The headline claim.
# ---------------------------------------------------------------------------


def test_a_derived_matte_tracks_the_moving_subject(tmp_path) -> None:
    """Two timestamps, two non-overlapping windows -- the acceptance of #17.

    The fixture's red box spans x=60..120 at t=0.5 and x=220..280 at t=2.5. A
    matte that tracks shows white in the first window and black in the second at
    t=0.5, and the exact reverse at t=2.5. A matte that is merely non-empty, or
    frozen on frame one, fails this.
    """
    matte = tmp_path / "matte.mkv"
    mask_from_video(str(ensure_moving_subject_clip()), str(matte), key="colorkey", colour="0x00FF00", similarity=0.10)

    early_at_start = _mean_luma(matte, 0.5, "crop=40:180:60:0")
    early_at_end = _mean_luma(matte, 0.5, "crop=40:180:220:0")
    late_at_start = _mean_luma(matte, 2.5, "crop=40:180:60:0")
    late_at_end = _mean_luma(matte, 2.5, "crop=40:180:220:0")

    assert early_at_start > 50, f"nothing white where the subject starts: {early_at_start}"
    assert early_at_end < 5, f"white where the subject has not arrived yet: {early_at_end}"
    assert late_at_end > 50, f"nothing white where the subject ends up: {late_at_end}"
    assert late_at_start < 5, f"white left behind where the subject has gone: {late_at_start}"


def test_white_is_what_survives_the_key(tmp_path) -> None:
    """The contract is white=keep. Keying the FIELD must keep the SUBJECT."""
    matte = tmp_path / "matte.mkv"
    mask_from_video(str(ensure_moving_subject_clip()), str(matte), key="colorkey", colour="0x00FF00", similarity=0.10)

    subject = _mean_luma(matte, 0.5, "crop=40:60:60:60")
    field = _mean_luma(matte, 0.5, "crop=40:60:0:0")

    assert subject > 200, f"the subject should be kept (white), not {subject}"
    assert field < 5, f"the keyed field should be dropped (black), not {field}"


def test_an_unknown_key_is_refused_naming_the_real_ones(tmp_path) -> None:
    with pytest.raises(VidError) as failure:
        mask_from_video(str(ensure_moving_subject_clip()), str(tmp_path / "x.mkv"), key="magic")

    message = str(failure.value)
    assert "magic" in message, message
    assert "colorkey" in message, message
    assert "lumakey" in message, message


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_a_fraction_outside_zero_to_one_is_refused(tmp_path, bad) -> None:
    with pytest.raises(VidError):
        mask_from_video(str(ensure_moving_subject_clip()), str(tmp_path / "x.mkv"), similarity=bad)


# ---------------------------------------------------------------------------
# THE COMPOSITION. One matte format, one producer, one consumer.
# ---------------------------------------------------------------------------


def test_what_mask_writes_is_what_overlay_reads(tmp_path) -> None:
    """The claim that makes this a verb rather than a second implementation.

    `vid mask` and `overlay --mask video` are only worth having as separate
    things if the file passes cleanly between them. Verb-level tests on each
    side would both pass while the pair was broken -- which is exactly how ten
    of twelve composed audio chains were once broken while every verb was
    correct alone.
    """
    from vid.lib import overlay, render
    from vid.plan import Plan

    source = ensure_moving_subject_clip()
    matte = tmp_path / "matte.mkv"
    mask_from_video(str(source), str(matte), key="colorkey", colour="0x00FF00", similarity=0.10)

    # `mask` is the KIND as a string, with the file in `mask_source`. Passing a
    # `Mask` object here instead produced a self-contradictory refusal --
    # "Unknown mask Mask(kind='video', ...). Use one of: ... video." -- because
    # the membership test compares against strings. Worth knowing before
    # reading that message as a library fault.
    plan = overlay(Plan(source=str(source)), str(source), mask="video", mask_source=str(matte))
    out = tmp_path / "composed.mp4"
    render(plan, str(out))

    assert out.is_file(), "the matte this tool wrote could not be consumed by its own overlay"
    _codec, width, height, _pix_fmt, _rate = _stream(out)
    assert (int(width), int(height)) == (320, 180), "the composed render changed the frame size"


def test_the_format_guard_fires_when_the_encoder_does_not_honour_gray(tmp_path, monkeypatch) -> None:
    """The read-back guard, tested by CAUSING the failure it exists to catch.

    THIS TEST EXISTS BECAUSE A MUTATION SURVIVED. Deleting the read-back check
    from `_write_matte` left all eleven tests above green, and they were right
    to be: on this ffmpeg the matte IS gray, so removing a check that never
    fires changes nothing observable. The check was real and untested -- the
    exact shape of a guard that quietly stops guarding.

    Forcing libx264 reproduces the measured failure. mkv/libx264 returns
    `yuvj420p` no matter what pixel format is requested, which is precisely the
    condition that made the first implementation of this verb wrong while
    reporting success.
    """
    monkeypatch.setattr("vid.lib.MATTE_CODEC", "libx264")

    with pytest.raises(VidError) as failure:
        mask_wipe("linear", str(tmp_path / "reveal.mkv"), width=320, height=180, seconds=1.0)

    message = str(failure.value)
    assert "yuvj420p" in message, f"the refusal does not name what came back instead: {message!r}"
    assert MATTE_PIX_FMT in message, f"the refusal does not name what was required: {message!r}"
