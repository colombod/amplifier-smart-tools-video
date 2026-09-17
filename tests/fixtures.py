"""Test clips, generated rather than committed.

Every assertion we make about these is about DURATION or about whether two
sources actually blended -- never about exact bytes. So encoder variation
between ffmpeg builds cannot affect a result, which removes the usual reason to
commit binary fixtures. And the tests that need video are exactly the tests that
need ffmpeg: absent it, they skip either way.

The clips are deliberately distinguishable, because a stitch test that cannot
tell clip A from clip B proves nothing:

- a flat, unmistakable colour per clip
- the clip's name burned in, large
- a running frame counter, so a retime is visible
- a distinct audio tone per clip, so `acrossfade` is checkable and not just `xfade`
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"

#: name -> (colour, hz, seconds). Tones are a fifth apart so a blend is audible
#: and, more usefully, separable by frequency in a spectrum check.
CLIPS: dict[str, tuple[str, int, float]] = {
    "alpha": ("red", 440, 3.0),
    "bravo": ("green", 660, 3.0),
    "charlie": ("blue", 880, 2.0),
}


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@dataclass(frozen=True)
class Clip:
    path: Path
    name: str
    colour: str
    hz: int
    seconds: float


def _has_filter(name: str) -> bool:
    """Whether this ffmpeg build carries a filter. Builds genuinely differ."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return any(line.split()[1:2] == [name] for line in out.stdout.splitlines() if line.strip())


def _build(name: str, colour: str, hz: int, seconds: float, path: Path) -> None:
    """Build one clip.

    NOTE ON PORTABILITY, which is load-bearing rather than incidental. `drawtext`
    needs fontconfig, which is usually present on Linux and macOS and frequently
    absent on Windows -- and where a fontfile must be named, Windows paths carry
    a drive-letter colon that ffmpeg's own filter syntax reads as a separator.
    That is a real trap and not a hypothetical one.

    So the label is a CONVENIENCE and the colour is the SIGNAL. Every assertion
    these fixtures support reads the colour; the burned-in text exists only so a
    human opening the file can tell the clips apart. When the filter is missing,
    the clip is still built and every test still works.
    """
    filters = []
    if _has_filter("drawtext"):
        filters.append(
            f"drawtext=text='{name.upper()}':fontsize=96:fontcolor=white:"
            f"x=(w-text_w)/2:y=(h-text_h)/2-60,"
            f"drawtext=text='%{{n}}':fontsize=64:fontcolor=white:"
            f"x=(w-text_w)/2:y=(h-text_h)/2+60"
        )

    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={colour}:s=640x360:r=30:d={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency={hz}:sample_rate=48000:duration={seconds}",
    ]
    if filters:
        command += ["-vf", ",".join(filters)]
    command += [
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-g", "15",  # a small GOP, so keyframe-aligned trimming is testable
        "-c:a", "aac", "-shortest",
        str(path),
    ]
    subprocess.run(command, check=True, capture_output=True)


def ensure_clips() -> dict[str, Clip]:
    """Build any clip that is missing; return them all. Idempotent and cheap."""
    if not have_ffmpeg():
        raise RuntimeError("ffmpeg and ffprobe must be on PATH to build fixtures")
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    clips: dict[str, Clip] = {}
    for name, (colour, hz, seconds) in CLIPS.items():
        path = FIXTURE_DIR / f"{name}.mp4"
        if not path.exists():
            _build(name, colour, hz, seconds, path)
        clips[name] = Clip(path=path, name=name, colour=colour, hz=hz, seconds=seconds)
    return clips


def probe_duration(path: Path | str) -> float:
    """Seconds, from ffprobe. The primary assertion for stitch and transition."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def frame_colour(path: Path | str, at: float) -> tuple[int, int, int]:
    """The average RGB of one frame, for proving a blend actually happened.

    Mid-transition, neither source's colour should be present alone -- that is
    the difference between a real crossfade and a hard cut, and it is not
    visible in the duration.
    """
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
         "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    )
    pixel = out.stdout[:3]
    if len(pixel) < 3:
        raise RuntimeError(f"no frame at {at}s in {path}")
    return (pixel[0], pixel[1], pixel[2])
