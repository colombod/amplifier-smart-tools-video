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

from dataclasses import dataclass
import math
from pathlib import Path
import shutil
import struct
import subprocess

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
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={colour}:s=640x360:r=30:d={seconds}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={hz}:sample_rate=48000:duration={seconds}",
    ]
    if filters:
        command += ["-vf", ",".join(filters)]
    command += [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "15",  # a small GOP, so keyframe-aligned trimming is testable
        "-c:a",
        "aac",
        "-shortest",
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


def ensure_undecodable_clip() -> Path:
    """A file ffprobe can read the duration of, but ffmpeg cannot decode a single
    frame from -- the real shape of "scene detection fails but duration probing
    succeeds", not a stand-in for it.

    Built with `-movflags +faststart` so the `moov` box (container metadata,
    including duration) sits BEFORE `mdat` (the encoded frame data). Zeroing
    everything from `mdat` onward destroys every frame while leaving the
    metadata ffprobe reads intact -- verified: `ffprobe -show_entries
    format=duration` still succeeds on the result, while ffmpeg's decoder
    exits non-zero on it.
    """
    path = FIXTURE_DIR / "undecodable.mp4"
    if path.exists():
        return path
    if not have_ffmpeg():
        raise RuntimeError("ffmpeg and ffprobe must be on PATH to build fixtures")
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    source = FIXTURE_DIR / "_undecodable_source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240:r=30:d=3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    data = bytearray(source.read_bytes())
    mdat_index = data.find(b"mdat")
    if mdat_index == -1:
        raise RuntimeError("expected an 'mdat' box in the generated clip -- ffmpeg's mp4 muxer changed shape")
    start = mdat_index + 8  # past the fourcc and its 4-byte size prefix
    for i in range(start, len(data)):
        data[i] = 0
    path.write_bytes(bytes(data))
    source.unlink()
    return path


def ensure_corner_clip() -> Clip:
    """A red clip with a small blue marker in its top-left corner, built once.

    Not part of `CLIPS` -- it exists to prove a CROP happened, not to be told
    apart in a stitch. A flat colour cannot show that: zooming toward centre
    on a solid-colour clip looks identical zoomed or not. A marker confined to
    one corner does not -- it is exactly what a centred crop pushes out of
    frame first, so sampling that corner tells "cropped" from "not cropped".
    """
    path = FIXTURE_DIR / "corner.mp4"
    if not path.exists():
        if not have_ffmpeg():
            raise RuntimeError("ffmpeg and ffprobe must be on PATH to build fixtures")
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=320x240:r=30:d=3",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=550:sample_rate=48000:duration=3",
                "-vf",
                "drawbox=x=0:y=0:w=40:h=40:color=blue:t=fill",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
    return Clip(path=path, name="corner", colour="red", hz=550, seconds=3.0)


def ensure_square_clip() -> Clip:
    """A 480x480 clip with a centred 120x120 white square, built once.

    Square on purpose: every clip in `CLIPS` is 640x360, so none of them can
    show what normalising a DIFFERENT aspect ratio did. 1:1 against 16:9 is the
    widest disagreement available, which puts the padding and the cropping
    where a pixel test can find them.

    The marker is what separates "fitted" from "stretched". A flat colour
    rescaled to the wrong aspect looks exactly like one rescaled to the right
    one; a square that is still square after the fact does not.
    """
    path = FIXTURE_DIR / "square.mp4"
    if not path.exists():
        if not have_ffmpeg():
            raise RuntimeError("ffmpeg and ffprobe must be on PATH to build fixtures")
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=480x480:r=30:d=2",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=330:sample_rate=48000:duration=2",
                "-vf",
                "drawbox=x=180:y=180:w=120:h=120:color=white:thickness=fill",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-g",
                "15",
                "-c:a",
                "aac",
                "-shortest",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
    return Clip(path=path, name="square", colour="blue", hz=330, seconds=2.0)


def marker_box(path: Path | str, at: float, width: int, height: int) -> tuple[int, int]:
    """The (width, height) of the white marker in a decoded frame, in pixels.

    Reads real pixels rather than trusting the filter graph: a chain can be
    accepted by ffmpeg and still stretch, and a stretched square is the exact
    thing `fit` and `fill` promise not to produce.
    """
    raw = subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-ss",
            str(at),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    columns = [x for x in range(width) if _is_marker(raw, width, x, height // 2)]
    rows = [y for y in range(height) if _is_marker(raw, width, width // 2, y)]
    if not columns or not rows:
        return (0, 0)
    return (max(columns) - min(columns) + 1, max(rows) - min(rows) + 1)


def frame_row(path: Path | str, at: float, y: int, width: int = 640) -> list[tuple[int, int, int]]:
    """A whole decoded row, in ONE ffmpeg call.

    `pixel_at` costs a process per pixel, which is fine for a handful of named
    points and ruinous for scanning: measuring a 640px row through it spawns 640
    ffmpeg invocations and turned one test file into four and a half minutes.
    Anything that walks a row wants this instead.
    """
    raw = subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-ss",
            str(at),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    start = y * width * 3
    return [(raw[i], raw[i + 1], raw[i + 2]) for i in range(start, start + width * 3, 3)]


def pixel_at(path: Path | str, at: float, x: int, y: int, width: int = 640) -> tuple[int, int, int]:
    """One decoded pixel, by coordinate.

    `frame_colour` averages the whole frame, which cannot tell "a layer covers
    this corner" from "the frame got slightly greener". Compositing needs a
    named point: inside the layer's box, and outside it.
    """
    raw = subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-ss",
            str(at),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    index = (y * width + x) * 3
    return (raw[index], raw[index + 1], raw[index + 2])


def _is_marker(raw: bytes, width: int, x: int, y: int) -> bool:
    index = (y * width + x) * 3
    return all(channel > 180 for channel in raw[index : index + 3])


def ensure_silent_clip() -> Clip:
    """A clip carrying picture and NO audio stream at all, built once.

    Not part of `CLIPS` -- every clip there has a tone, which is what makes them
    separable in a mix. This one exists for the opposite reason: a file with no
    audio stream is a distinct state from one whose audio is quiet, and only the
    former makes a compiler interpolate a stream specifier that matches nothing.
    """
    path = FIXTURE_DIR / "silent.mp4"
    if not path.exists():
        if not have_ffmpeg():
            raise RuntimeError("ffmpeg and ffprobe must be on PATH to build fixtures")
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=yellow:s=640x360:r=30:d=2",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-g",
                "15",
                "-an",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
    return Clip(path=path, name="silent", colour="yellow", hz=0, seconds=2.0)


def corner_pixel(path: Path | str, at: float) -> tuple[int, int, int]:
    """Average RGB of a small patch at the very corner (0,0) of one frame.

    Blue while the marker is still in frame, red once a crop has pushed it
    out -- a `-vf scale=1:1` average of the WHOLE frame (as `frame_colour`
    takes) would dilute a 40x40 marker in a 320x240 frame past the point of
    a reliable assertion; restricting the average to a small patch at the
    marker's own corner keeps the signal undiluted.
    """
    out = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(at),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "crop=4:4:2:2,scale=1:1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    if len(out) < 3:
        raise RuntimeError(f"no frame at {at}s in {path}")
    return (out[0], out[1], out[2])


def probe_duration(path: Path | str) -> float:
    """Seconds, from ffprobe. The primary assertion for stitch and transition."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


def frame_colour(path: Path | str, at: float) -> tuple[int, int, int]:
    """The average RGB of one frame, for proving a blend actually happened.

    Mid-transition, neither source's colour should be present alone -- that is
    the difference between a real crossfade and a hard cut, and it is not
    visible in the duration.
    """
    out = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(at),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=1:1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    pixel = out.stdout[:3]
    if len(pixel) < 3:
        raise RuntimeError(f"no frame at {at}s in {path}")
    return (pixel[0], pixel[1], pixel[2])


def has_audio_stream(path: Path | str) -> bool:
    """Whether the file carries an audio stream at all -- not whether it is
    silent. A silent track and no track look identical in a player and are
    different files; only ffprobe's stream list tells them apart."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return "audio" in out


def tone_strength(path: Path | str, hz: int, at: float = 0.5, window: float = 0.3) -> float:
    """How strongly `hz` is present in a window of `path`'s audio, relative to
    the loudest of the fixtures' own three tones (440/660/880Hz).

    Goertzel: one bin of a DFT, computed directly. Cheaper than an FFT and it
    is the only bin any caller here ever wants.
    """
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(at),
            "-t",
            str(window),
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
    ).stdout
    count = len(raw) // 2
    if count < 256:
        return 0.0
    samples = struct.unpack(f"<{count}h", raw[: count * 2])

    def power(freq: int) -> float:
        k = 2 * math.cos(2 * math.pi * freq / 8000)
        s1 = s2 = 0.0
        for sample in samples:
            s0 = sample + k * s1 - s2
            s2, s1 = s1, s0
        return math.sqrt(abs(s1 * s1 + s2 * s2 - k * s1 * s2)) / count

    strongest = max(power(f) for f in (440, 660, 880)) or 1.0
    return power(hz) / strongest
