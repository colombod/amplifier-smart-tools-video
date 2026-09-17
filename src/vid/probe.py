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
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise VidError(f"ffprobe could not read {path!r}: {result.stderr.strip() or 'no reason given'}")
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise VidError(f"{path!r} has no readable duration -- is it a video file?") from exc


def has_audio(path: str) -> bool:
    """Whether the file carries an audio stream at all.

    Load-bearing, and it was missing. The compiler assumed `0:a` existed on every
    source, so ANY video without an audio track failed at render with
    `Stream map '' matches no streams` -- an ffmpeg message that names neither
    the file nor the reason. Screen recordings routinely have no audio, and they
    are one of the commonest things this tool is pointed at.

    Returns True when it cannot tell: guessing "there is audio" degrades to the
    old behaviour for an unreadable file, while guessing "there is none" would
    silently DROP the audio from a file that has it. Losing sound is worse than
    a loud failure.
    """
    if not have_ffprobe():
        return True
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return True
    return "audio" in result.stdout


def has_subtitles_filter() -> bool:
    """Whether this ffmpeg was built with subtitle rendering (libass).

    REPORTED BY A REAL USER, on macOS, via an agent that installed the tool from
    the catalog and then hit it. `caption` burns subtitles into the picture with
    ffmpeg's `subtitles` filter, and that filter only exists when ffmpeg was
    COMPILED against libass. Plenty of builds are not -- several Homebrew taps
    ship a slimmed one, and most minimal container images do.

    So `ffmpeg` on PATH is necessary and not sufficient, and the manifest saying
    "ffmpeg" was telling a half-truth. Every other verb works on a slim build;
    exactly one does not.
    """
    if not shutil.which("ffmpeg"):
        return False
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True
    )
    if result.returncode != 0:
        return False
    return any(
        line.split()[1] == "subtitles"
        for line in result.stdout.splitlines()
        if len(line.split()) > 1
    )
