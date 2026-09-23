"""Checking a rendered video against properties, with no model involved.

`render` produces a file and the caller has no way to know it is right. A person
opens it and looks. An AGENT editing unattended cannot -- so an edit that renders
successfully and is silently wrong is indistinguishable from a correct one. That
is the gap this closes.

NAMED PROPERTIES, NOT FREE-TEXT CLAIMS. "Does the logo appear early?" needs a
vision index and is a different, much larger thing. Everything here is decided by
ffprobe and frame arithmetic: deterministic, free, offline, and no provider.

The blend check is the one worth reading. It distinguishes a real crossfade from
a hard cut by sampling three frames and asking whether the middle one resembles
NEITHER side. That is the whole trick, and it is why the transition-generation
experiment does not need a model to grade its own output.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import subprocess

from vid.schemas import VidError


@dataclass(frozen=True)
class Check:
    """One property, and what was actually measured."""

    name: str
    held: bool
    expected: str
    measured: str
    note: str = ""

    def line(self) -> str:
        mark = "ok  " if self.held else "FAIL"
        tail = f"  -- {self.note}" if self.note else ""
        return f"  [{mark}] {self.name:<22} expected {self.expected:<18} measured {self.measured}{tail}"


def _ffprobe(path: str, *args: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json", *args, path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise VidError(
            f"ffprobe could not read {path!r}: {result.stderr.strip() or 'no reason given'}\n"
            "Verify the path is a video file ffprobe can open (try `ffprobe` on it directly), "
            "or run `vid check`."
        )
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise VidError(
            f"ffprobe returned something that is not JSON for {path!r}: {exc}\n"
            "Run `vid check` to confirm ffprobe is working, or try `ffprobe` on the file directly."
        ) from exc


def _rgb_at(path: str, at: float) -> tuple[int, int, int]:
    """The average colour of one frame, as a 1x1 downscale.

    Scaling the whole frame to a single pixel is the cheapest honest summary: it
    is the mean, so a blend of two flat clips lands between them, and a cut lands
    on one or the other.
    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(at),
            "-i",
            path,
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
        capture_output=True,
    )
    pixel = result.stdout[:3]
    if result.returncode != 0:
        raise VidError(f"ffmpeg frame analysis failed for {path!r}: {result.stderr.decode(errors='replace')}")
    if len(pixel) < 3:
        raise VidError(
            f"There is no frame at {at}s in {path!r}. Check the video's actual duration "
            "with `vid verify --expect-duration`, or pick a smaller offset."
        )
    return (pixel[0], pixel[1], pixel[2])


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) ** 0.5


def duration(path: str) -> float:
    data = _ffprobe(path, "-show_entries", "format=duration")
    try:
        return float(data["format"]["duration"])
    except (KeyError, ValueError) as exc:
        raise VidError(
            f"{path!r} has no readable duration. Verify it is a video file ffprobe can open "
            "(try `ffprobe` on it directly), or run `vid check`."
        ) from exc


def check_duration(path: str, expected: float, tolerance: float = 0.15) -> Check:
    measured = duration(path)
    return Check(
        "duration",
        abs(measured - expected) <= tolerance,
        f"{expected:.2f}s +/-{tolerance}",
        f"{measured:.2f}s",
    )


def check_resolution(path: str, expected: str) -> Check:
    data = _ffprobe(path, "-select_streams", "v:0", "-show_entries", "stream=width,height")
    streams = data.get("streams") or []
    if not streams:
        return Check("resolution", False, expected, "no video stream")
    measured = f"{streams[0].get('width')}x{streams[0].get('height')}"
    return Check("resolution", measured == expected.lower().strip(), expected, measured)


def check_audio(path: str) -> Check:
    """Present AND not silent. A track of digital silence passes the first test."""
    data = _ffprobe(path, "-select_streams", "a:0", "-show_entries", "stream=codec_name")
    if not (data.get("streams") or []):
        return Check("audio", False, "present, not silent", "no audio stream")

    # `-v info`, NOT `-v error`. volumedetect reports its measurement on an info
    # line, so quietening ffmpeg the way every other call here does would discard
    # the one thing this check is reading -- and the failure is silent: the check
    # reports "could not measure level" and --expect-audio fails on every file,
    # including correct ones. Caught by running it, invisible to a code read.
    result = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    mean = None
    if result.returncode != 0:
        raise VidError(f"ffmpeg audio analysis failed for {path!r}: {result.stderr.strip()}")
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            mean = float(line.split("mean_volume:")[1].strip().split()[0])
    if mean is None:
        return Check("audio", False, "present, not silent", "could not measure level")
    # -91 dB is the floor for 16-bit digital silence; anything at or below it is
    # a track that exists and carries nothing, which is a real failure mode.
    return Check("audio", mean > -90.0, "present, not silent", f"mean {mean:.1f} dB")


def check_transition_at(path: str, at: float, window: float = 0.5) -> Check:
    """A real blend, or a hard cut dressed as one.

    Sample before, during and after. If the middle frame resembles NEITHER side,
    something genuinely mixed them. If it matches one of them, the picture cut.

    The threshold is relative to how different the two sides are, so this works
    for clips that differ a lot and correctly refuses to claim a blend between two
    clips that look nearly identical -- where no measurement could tell.
    """
    before = _rgb_at(path, max(0.0, at - window))
    middle = _rgb_at(path, at)
    after = _rgb_at(path, at + window)

    separation = _distance(before, after)
    if separation < 30:
        return Check(
            "transition",
            False,
            f"a blend at {at}s",
            f"sides differ by only {separation:.0f}",
            "the two clips look too alike here for a blend to be detectable",
        )

    to_before = _distance(middle, before)
    to_after = _distance(middle, after)
    # A blended frame sits between the two, so it is some distance from BOTH.
    # A cut puts it on top of one of them.
    blended = to_before > separation * 0.2 and to_after > separation * 0.2
    return Check(
        "transition",
        blended,
        f"a blend at {at}s",
        f"mid-frame {to_before:.0f} from before, {to_after:.0f} from after",
        "" if blended else "the middle frame matches one side -- this is a hard cut",
    )


def check_no_black_frames(
    path: str, longest: float = 0.5, pixel_threshold: float = 0.1, frame_threshold: float = 0.98
) -> Check:
    """Black is how a mis-timed edit usually shows itself."""
    for name, value in (("pixel_threshold", pixel_threshold), ("frame_threshold", frame_threshold)):
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise VidError(f"{name} must be a finite fraction from 0 to 1.")
    if not math.isfinite(longest) or longest < 0:
        raise VidError("longest_black must be finite, nonnegative seconds.")
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "info",
            "-xerror",
            "-copyts",
            "-i",
            path,
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            f"blackdetect=d=0:pix_th={pixel_threshold}:pic_th={frame_threshold},metadata=print:file=-",
            "-progress",
            "pipe:1",
            "-nostats",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    frames = [
        int(line.split("=", 1)[1])
        for line in result.stdout.splitlines()
        if line.startswith("frame=") and line.split("=", 1)[1].strip().isdigit()
    ]
    if result.returncode != 0 or not frames or max(frames) == 0:
        raise VidError(
            f"ffmpeg black-frame analysis failed for {path!r}; no complete measurement. "
            f"Check the input and run `vid check`.\n{result.stderr.strip()}"
        )
    worst = 0.0
    for line in result.stderr.splitlines():
        if "black_duration" in line:
            for token in line.split():
                if token.startswith("black_duration:"):
                    worst = max(worst, float(token.split(":")[1]))
    # blackdetect logs an open EOF run only through the last frame's PTS,
    # not its presentation end. Metadata emits black_end only on a real
    # nonblack frame, so an unmatched start identifies exactly the EOF case.
    terminal_start = None
    for line in result.stdout.splitlines():
        if line.startswith("lavfi.black_start="):
            terminal_start = float(line.split("=", 1)[1])
        elif line.startswith("lavfi.black_end="):
            terminal_start = None
    if terminal_start is not None:
        from vid.probe import picture_presentation_bounds

        _, end = picture_presentation_bounds(path)
        worst = max(worst, end - terminal_start)
    return Check(
        "no-black-frames",
        worst <= longest + 1e-9,
        f"none longer than {longest}s",
        f"longest {worst:.2f}s" if worst else "none found",
        f"pix_th={pixel_threshold:g}, pic_th={frame_threshold:g}, longest_black={longest:g}s",
    )


def report(checks: list[Check]) -> tuple[str, bool]:
    """The lines a caller reads, and whether everything held."""
    if not checks:
        raise VidError(
            "verify was given nothing to check. Name at least one expectation -- `vid verify --help` lists them."
        )
    passed = all(check.held for check in checks)
    lines = [f"{'all properties hold' if passed else 'VIOLATIONS FOUND'}", ""]
    lines += [check.line() for check in checks]
    if not passed:
        lines += ["", "  Nothing here involved a model. These are measurements, not opinions."]
    return "\n".join(lines), passed
