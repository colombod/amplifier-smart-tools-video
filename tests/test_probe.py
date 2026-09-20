"""Every ffprobe/ffmpeg failure here must name what went wrong and the remedy.

A caller should never have to infer the fix from a raw ffprobe stderr line, or
worse, from a bare traceback because the binary was never checked for at all.
"""

from __future__ import annotations

import pytest

from tests.fixtures import have_ffmpeg
from vid.probe import duration
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="these run real ffprobe")


def test_duration_on_an_unreadable_file_names_the_remedy(tmp_path):
    """ffprobe running and failing (as opposed to not being on PATH at all)
    must still leave the caller with a next action, not just the raw
    diagnostic."""
    not_a_video = tmp_path / "not-a-video.mp4"
    not_a_video.write_bytes(b"this is not a video container")

    with pytest.raises(VidError) as failure:
        duration(str(not_a_video))

    message = str(failure.value)
    assert "ffprobe" in message
    assert "vid check" in message or "ffprobe" in message.split("\n")[-1], (
        "a failure that does not point at a next action is half a failure"
    )


def test_duration_with_no_ffprobe_on_path_names_the_remedy(monkeypatch, tmp_path):
    """The presence check that was already here, kept honest: a missing
    binary must never reach subprocess.run at all."""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    with pytest.raises(VidError) as failure:
        duration("whatever.mp4")

    message = str(failure.value)
    assert "ffprobe" in message.lower() or "ffmpeg" in message.lower()
    assert "vid check" in message
