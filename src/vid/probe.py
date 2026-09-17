"""Asking ffprobe how long something is.

Kept apart from `compile` on purpose. Compiling a plan is pure -- same plan, same
argv, no filesystem, no subprocess -- and that is what lets every verb except
`render` work with no ffmpeg installed.

But `xfade` needs an ABSOLUTE offset measured from the start of its first input,
and that offset is the running duration of the edit so far. Durations are a
property of the files, not of the plan. So the probe happens on the render path,
where ffmpeg is present anyway, and the answer is handed to the compiler.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from vid.schemas import VidError


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def have_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None


def duration(path: str) -> float:
    """Seconds, from the container metadata."""
    if not have_ffprobe():
        raise VidError(
            "ffprobe is not on PATH, and a transition needs to know how long the clips are. "
            "Install ffmpeg (it ships ffprobe) -- see `vid check` for the command for your system."
        )
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise VidError(f"ffprobe could not read {path!r}: {result.stderr.strip() or 'no reason given'}")
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise VidError(f"{path!r} has no readable duration -- is it a video file?") from exc
