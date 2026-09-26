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
import math
import shutil
import subprocess

from vid.schemas import VidError


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def have_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None


def _refuse_missing(missing: list[str], what_for: str) -> None:
    """The one sentence every prerequisite refusal in this tool is built from.

    ALWAYS CARRIES THE MANIFEST'S INSTALL REFERENCE. The spec requires the
    failure and the manifest to agree, and `check-spec-adherence` run 9 found
    four refusals that named neither the install URL nor anything but
    "Run `vid check`" -- while the manifest declares both binaries with
    `https://ffmpeg.org/download.html`. Reading it from `manifest_install`
    rather than repeating the string means they cannot drift apart.
    """
    from vid.core.manifest import manifest_install

    named = " and ".join(missing)
    verb = "is" if len(missing) == 1 else "are"
    pronoun = "it" if len(missing) == 1 else "them"
    raise VidError(
        f"{named} {verb} not on PATH, and {what_for}. "
        f"Install {pronoun} ({manifest_install('ffmpeg')}) -- ffmpeg ships ffprobe with it. "
        "See `vid check` for the command for your system."
    )


def require_ffprobe(what_for: str) -> None:
    """For the sites that need ffprobe alone -- durations and stream metadata."""
    if not have_ffprobe():
        _refuse_missing(["ffprobe"], what_for)


def require_ffmpeg(what_for: str) -> None:
    """For the sites that decode or encode, and so need ffmpeg alone."""
    if not have_ffmpeg():
        _refuse_missing(["ffmpeg"], what_for)


def require_ffmpeg_tools(what_for: str) -> None:
    """Refuse when either binary is missing, NAMING THE ONE THAT IS ABSENT.

    ONE RULE FOR A CLASS WITH FOUR MEMBERS. Four places guarded on both
    binaries and each wrote its own sentence, so each was free to be wrong in
    its own way -- and three of them were:

        index.py            said "ffmpeg is not on PATH" whichever was missing
        lib.verify          said "it needs both", naming neither as the absent one
        lib.index           same
        color.py            correct, and only because it was reported

    `check-spec-adherence` reported `color` on run 6 and `index` on run 7, one
    instance at a time, each time pointing at the previously-fixed one as the
    model to copy. Copying it a third time would have left `verify` and
    `lib.index` still wrong and waiting for run 8. The spec asks the failure to
    name what is absent; that is a property of the CLASS, so it is enforced in
    one place.

    Measured, with ffmpeg alone on PATH, before this existed:
        ffmpeg present: True | ffprobe present: False
        "ffmpeg is not on PATH, and recolor needs it to sample colour..."
    -- sending the caller to install something they already had. They ship as
    separate packages on several distributions, so this is a real arrangement.

    `what_for` completes the sentence "..., and {what_for}." so each caller
    keeps its own explanation of why it needs them.
    """
    missing = [name for name, present in (("ffmpeg", have_ffmpeg()), ("ffprobe", have_ffprobe())) if not present]
    if not missing:
        return
    _refuse_missing(missing, what_for)


def duration(path: str) -> float:
    """Seconds, from the container metadata."""
    require_ffprobe("a transition needs to know how long the clips are")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
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
        return float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise VidError(
            f"{path!r} has no readable duration. Verify it is a video file ffprobe can open "
            "(try `ffprobe` on it directly), or run `vid check`."
        ) from exc


def picture_presentation_bounds(path: str) -> tuple[float, float]:
    """Decoded picture's first PTS and final presentation end, including the last frame."""
    require_ffprobe("picture timing is read from the container's own metadata")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp_time,duration_time,pkt_duration_time",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
    )
    try:
        frames = json.loads(result.stdout)["frames"]
        timed = sorted(frames, key=lambda frame: float(frame["best_effort_timestamp_time"]))
        first = float(timed[0]["best_effort_timestamp_time"])
        last = timed[-1]
        frame_duration = float(last.get("duration_time", last.get("pkt_duration_time", "nan")))
        end = float(last["best_effort_timestamp_time"]) + frame_duration
        valid = (
            result.returncode == 0
            and not result.stderr.strip()
            and math.isfinite(first)
            and math.isfinite(end)
            and frame_duration > 0
            and end > first
        )
    except (KeyError, IndexError, ValueError, TypeError):
        valid, first, end = False, 0.0, 0.0
    if not valid:
        raise VidError(
            f"Could not measure final picture presentation time for {path!r}. Run `vid check`.\n{result.stderr}"
        )
    return first, end


def video_duration(path: str) -> float:
    """Picture duration, never the duration of a longer audio stream."""
    require_ffprobe("picture timing is read from the container's own metadata")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration", "-of", "json", path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise VidError(f"ffprobe could not read picture timing for {path!r}. Run `vid check`.\n{result.stderr}")
    try:
        total = float(json.loads(result.stdout)["streams"][0]["duration"])
        if math.isfinite(total) and total > 0:
            return total
    except (KeyError, IndexError, ValueError, TypeError):
        pass
    first, end = picture_presentation_bounds(path)
    return end - first


def copy_video_duration(path: str) -> float:
    """Copy cannot retime packets: require a zero-based, measured picture stream."""
    require_ffprobe("picture stream-copy has to read the source's packet layout first")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v",
            "-show_entries",
            "stream=start_time,duration",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
    )
    try:
        streams = json.loads(result.stdout)["streams"]
        stream = streams[0]
        start, total = float(stream["start_time"]), float(stream["duration"])
        valid = result.returncode == 0 and len(streams) == 1 and math.isfinite(total) and total > 0 and start == 0
    except (KeyError, IndexError, ValueError, TypeError):
        valid, total = False, 0.0
    if not valid:
        raise VidError(
            "Picture stream-copy requires exactly one video stream starting at zero with a known positive duration. "
            "Use libx264 for this source. " + result.stderr.strip()
        )
    return total


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
            path,
        ],
        capture_output=True,
        text=True,
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
    result = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    if result.returncode != 0:
        return False
    return any(line.split()[1] == "subtitles" for line in result.stdout.splitlines() if len(line.split()) > 1)


def dimensions(path: str) -> tuple[int, int] | None:
    """The video's (width, height), or None when it cannot be read.

    Needed because `zoom` pins `zoompan`'s `s=` to the source's own size --
    with no explicit size that option silently defaults to `hd720`
    regardless of the source, which turned a 640x360 clip into 320x180
    once a trailing `scale` compounded it. It must be the SOURCE's own
    dimensions, not a guess: hardcoding one would silently resize footage
    while appearing to fix a bug.
    """
    if not have_ffprobe():
        return None
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            path,
        ],
        capture_output=True,
        text=True,
    )
    raw = result.stdout.strip()
    if result.returncode != 0 or not raw:
        return None
    try:
        width_str, _, height_str = raw.partition(",")
        width, height = int(width_str), int(height_str)
    except ValueError:
        return None
    return (width, height) if width > 0 and height > 0 else None


def frame_rate(path: str) -> float | None:
    """The video's nominal frame rate, or None when it cannot be read.

    Needed because `setpts` rescales timestamps WITHOUT putting frames back on a
    uniform grid, so a 3.0s clip at 2x came out 1.567s instead of 1.500s -- a
    4.7% overshoot that nothing flagged. Appending `fps=<this>` resamples them
    and lands it exactly.

    It must be the SOURCE's rate, not a constant: hardcoding 30 would silently
    convert a 25fps clip while appearing to fix a bug.
    """
    if not have_ffprobe():
        return None
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "default=nw=1:nk=1",
            path,
        ],
        capture_output=True,
        text=True,
    )
    raw = result.stdout.strip()
    if result.returncode != 0 or not raw:
        return None
    try:
        numerator, _, denominator = raw.partition("/")
        rate = float(numerator) / float(denominator or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None
