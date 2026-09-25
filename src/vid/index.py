"""The index: a time-coded account of what is IN a video.

Answering "where does she explain pricing?" needs something the plan cannot
supply -- a record of what was said and when. Building that record is the
expensive part, so it is built once and reused: ten questions about one video
cost one transcription.

WHERE THE GUARD LIVES. Every chunk here carries a time range produced by the
TRANSCRIBER, not by a model. `find` shows a model these chunks and asks which
ones, by id; the timestamp is then a dictionary lookup. A model is never in a
position to emit a time at all, so the failure mode this tool most fears -- a
plausible timestamp indistinguishable from a correct one -- is unreachable
rather than merely discouraged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import itertools
import json
from pathlib import Path
import subprocess

from vid.probe import have_ffmpeg, have_ffprobe
from vid.schemas import DEFAULT_INTELLIGENCE_MODEL, ReasoningEffort, VidError

INDEX_FORMAT = 1


def _require_ffmpeg_tools() -> None:
    """Refuse loudly, naming the remedy, rather than let a missing binary
    escape as a bare `FileNotFoundError` from inside `subprocess.run`.

    The install guidance matches `probe.duration`'s and the manifest's own
    entry for ffmpeg (`SMART_TOOL.md`): ffmpeg ships ffprobe, so one message
    covers both.
    """
    if not have_ffmpeg() or not have_ffprobe():
        raise VidError(
            "ffmpeg is not on PATH, and indexing needs it to detect shots and read durations. "
            "Install ffmpeg (it ships ffprobe) -- see `vid check` for the command for your system."
        )


def index_dir() -> Path:
    """Where indexes accumulate. Shared on purpose: the point is reuse."""
    import os

    override = os.environ.get("VID_INDEX_DIR")
    if override:
        # RESOLVED, same as `render`'s and `audio_extract`'s output paths: a
        # relative override reported back to a caller reading from a
        # different cwd would name a location only this process could find.
        return Path(override).resolve()
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "vid" / "index"


@dataclass(frozen=True)
class Chunk:
    """One passage of speech, with the time the transcriber gave it."""

    id: str
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Shot:
    id: str
    start: float
    end: float


def fingerprint(video: str) -> str:
    """Identify a video by its bytes, not its name.

    A path is not an identity: `talk.mp4` re-exported is a different video, and a
    renamed file is the same one. Hashing the head, the tail and the size is
    cheap and wrong only for files that are byte-identical apart from the middle,
    which does not happen to encoded video in practice.
    """
    path = Path(video)
    # THE FOURTH SIBLING, and it was found by a test rather than by reading:
    # monkeypatching `Path.is_file` to refuse surfaced this site alongside the
    # one in `load`. "No such video" is the WRONG sentence for a file that
    # exists and cannot be read, and without the guard a video inside an
    # unreadable directory escaped as a raw PermissionError traceback from
    # whichever of `is_file`, `stat` or `open` reached the filesystem first.
    try:
        present = path.is_file()
    except OSError as exc:
        raise VidError(
            f"The video at {video} could not be read: {exc.strerror or exc}. "
            "Check the permissions on the file and on every directory above it."
        ) from exc
    if not present:
        raise VidError(f"No such video: {video!r}. Check the path is correct and relative to the current directory.")
    try:
        size = path.stat().st_size
        digest = hashlib.sha256(str(size).encode())
        with path.open("rb") as handle:
            digest.update(handle.read(1 << 20))
            if size > (1 << 21):
                handle.seek(-(1 << 20), 2)
                digest.update(handle.read(1 << 20))
    except OSError as exc:
        raise VidError(
            f"The video at {video} could not be read: {exc.strerror or exc}. "
            "Check the permissions on the file and on every directory above it."
        ) from exc
    return digest.hexdigest()[:16]


def index_path(video: str) -> Path:
    return index_dir() / f"{fingerprint(video)}.json"


def _unreadable(path: Path, exc: OSError) -> str:
    """The same sentence `save` gives, for the read side.

    One wording for one cause: whichever end refused, the location came from
    the same place and the caller has the same one knob.
    """
    return (
        f"The index at {path} could not be read: {exc.strerror or exc}.\n"
        "That location comes from VID_INDEX_DIR when it is set, and a cache "
        "directory beside the video otherwise. Point VID_INDEX_DIR at a "
        "readable directory, or fix the permissions on this one."
    )


def load(video: str) -> dict | None:
    """The stored index for this video, or None when there is not one yet.

    THE THIRD SIBLING OF A CLASS FIXED TWICE ON THIS BRANCH. `save` already
    names `VID_INDEX_DIR` when the filesystem refuses a write, and the corrupt
    JSON case below already names the file and its remedy -- but the two
    filesystem calls in the READ path were bare. An unreadable index directory
    (a chmod 000 mount point, a VID_INDEX_DIR pointing inside one) came out of
    `is_file` as a raw traceback:

        PermissionError: [Errno 13] Permission denied:
            '/tmp/vidperm/locked/bf6781c471cbcf0d.json'

    `cli.main` only translates `VidError` into a named, non-zero exit, so
    anything else reaches the user as a stack trace naming neither the setting
    that chose the location nor anything they could do about it.

    NOT MERGED INTO ONE `try`. A refusal to look and a refusal to read are the
    same remedy, but "does it exist" must still answer None rather than raising
    when the answer is honestly no.
    """
    path = index_path(video)
    try:
        present = path.is_file()
    except OSError as exc:
        raise VidError(_unreadable(path, exc)) from exc
    if not present:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VidError(_unreadable(path, exc)) from exc
    except json.JSONDecodeError as exc:
        # A corrupt or truncated index must never escape as a bare traceback --
        # `cli.main` only translates `VidError` into a named, non-zero exit.
        # Naming the path AND the remedy is what turns this from "the tool
        # crashed" into "remove this one file and rebuild it".
        raise VidError(
            f"The index at {path} is not valid JSON ({exc}). Remove it and run `vid index {video}` again to rebuild it."
        ) from exc


#: How different two frames must be to count as a cut.
#:
#: 0.05, and NOT the 0.4 this shipped with, because 0.4 was measured missing
#: obvious hard cuts. Straight colour-and-title changes score as low as 0.076:
#:
#:     a three-shot video    cuts scored 0.399 and 0.193  -> 0.4 found ONE shot
#:     another three-shot    cuts scored 0.076 and 0.467  -> 0.4 found TWO
#:
#: The costs are ASYMMETRIC, and that is what settles the number. Over-detecting
#: splits one shot into two: one extra frame described, cheap, and a caller can
#: still find the moment. Under-detecting loses a boundary entirely -- the moment
#: cannot be found, and narration gets one giant slot instead of several. There
#: is no recovering from the second, so the threshold leans loose.
#:
#: This mattered more than it looked. Shot detection underpins BOTH the vision
#: story (a few dozen frames instead of eighteen thousand) and narration's slots.
#: A 29-second fixture reporting one shot looked plausible and was wrong.
SCENE_THRESHOLD = 0.05


def detect_shots(video: str, threshold: float = SCENE_THRESHOLD) -> list[Shot]:
    """Shot boundaries, deterministically and for free.

    This is the cheapest useful thing in the whole tool: no model, no network, no
    credentials, and it takes the visual problem from eighteen thousand frames
    down to a few dozen -- one per shot. Describing forty frames costs cents;
    describing six hundred does not.
    """
    _require_ffmpeg_tools()
    result = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", video, "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # A NONZERO EXIT MUST NEVER FALL BACK TO THE [0.0, duration] DEFAULT.
        # That default is meant for a video that genuinely has one shot; if it
        # is used instead because ffmpeg could not read the frames, the two
        # are indistinguishable in the written index. Refusing here, before
        # `build()` ever constructs a record, is what keeps them apart.
        detail = (result.stderr or "").strip().splitlines()
        raise VidError(
            f"Shot detection failed for {video!r} (ffmpeg exited {result.returncode}): "
            f"{detail[-1] if detail else 'ffmpeg gave no reason'}.\n"
            "Refusing to fall back to a single shot spanning the whole video -- that would be "
            "indistinguishable from a real one-shot result. Verify the file with `ffprobe`, or try `vid check`."
        )
    times = [0.0]
    for line in result.stderr.splitlines():
        if "pts_time:" in line:
            for token in line.split():
                if token.startswith("pts_time:"):
                    times.append(float(token.split(":")[1]))
    total = _duration(video)
    times.append(total)
    times = sorted({round(t, 3) for t in times if 0 <= t <= total})
    return [Shot(id=f"s{i}", start=a, end=b) for i, (a, b) in enumerate(itertools.pairwise(times))]


def _duration(video: str) -> float:
    _require_ffmpeg_tools()
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video,
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError as exc:
        raise VidError(
            f"{video!r} has no readable duration. Verify it is a video file ffprobe can open "
            "(try `ffprobe` on it directly), or run `vid check`."
        ) from exc


def transcribe(video: str, model_size: str = "base") -> list[Chunk]:
    """Speech to time-coded chunks, locally.

    Segment-level timing, which is what faster-whisper gives and what this needs:
    the job is finding a REGION, and a lossless cut lands on a keyframe anyway, so
    sub-second word precision would be discarded by the very next constraint.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise VidError(
            "No speech backend installed. Transcription needs one:\n"
            "  uv tool install --force 'vid[speech] @ "
            "git+https://github.com/colombod/amplifier-smart-tools-video'\n"
            "Every mechanical verb keeps working without it -- `vid check` shows what you have."
        ) from exc

    # The ImportError above is handled; CONSTRUCTING the model was not. An
    # unknown `--model` size, a failed weight download and an onnxruntime built
    # for another architecture all fail here, and `main()` catches only
    # VidError -- so each reached the user as a traceback.
    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(video, beam_size=1)
    except Exception as exc:
        raise VidError(
            f"The speech model {model_size!r} could not transcribe {video!r}: {exc}\n"
            "Sizes are tiny, base, small and medium -- check the spelling, and confirm the "
            "machine can reach the model download. `vid check` reports the speech backend."
        ) from exc
    return [
        Chunk(id=f"c{i}", start=round(segment.start, 3), end=round(segment.end, 3), text=segment.text.strip())
        for i, segment in enumerate(segments)
    ]


def describe(
    video: str,
    record: dict,
    intelligence,
    model: str = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: ReasoningEffort = "low",
) -> dict:
    """Add a description to every shot that does not have one.

    Descriptions live ON the shots, beside time ranges ffmpeg already produced.
    A model never supplies a time -- that is the whole point of describing frames
    rather than asking when something happened.

    EVERY PENDING SHOT OR THE RECORD IS UNCHANGED. `describe_shots` already
    refuses to return a partial set, but that guarantee is enforced here too,
    explicitly, rather than trusted implicitly -- a record this function writes
    must never leave a shot that WAS attempted looking identical to one that
    was not.
    """
    from vid.vision import describe_shots

    shots = record.get("shots") or []
    pending = [shot for shot in shots if not shot.get("description")]
    if not pending:
        return record

    described = describe_shots(video, pending, intelligence, model=model, reasoning_effort=reasoning_effort)
    by_id = {item.shot_id: item.description for item in described}
    missing = [shot["id"] for shot in pending if shot["id"] not in by_id]
    if missing:
        raise VidError(
            f"{len(missing)} of {len(pending)} pending shot(s) came back with no description: "
            f"{', '.join(missing)}. Refusing to record a partial result -- re-run `vid index --vision`."
        )
    for shot in shots:
        if shot["id"] in by_id:
            shot["description"] = by_id[shot["id"]]
    record["shots"] = shots
    return record


def build(video: str, *, speech: bool = True, shots: bool = True, model_size: str = "base") -> dict:
    """Build (or extend) the index for a video, and write it where it persists."""
    record = load(video) or {
        "index_format": INDEX_FORMAT,
        "video": str(Path(video).resolve()),
        "fingerprint": fingerprint(video),
        "duration": _duration(video),
    }
    if shots and "shots" not in record:
        record["shots"] = [asdict(shot) for shot in detect_shots(video)]
    if speech and "speech" not in record:
        record["speech"] = [asdict(chunk) for chunk in transcribe(video, model_size)]

    save(video, record)
    return record


def save(video: str, record: dict) -> Path:
    """Persist the index, naming the remedy when the filesystem refuses.

    THE ONE WRITE PATH FOR THE INDEX, because there were two. `build` wrote it
    here and `lib.index` wrote it again after `describe`, both with a bare
    `mkdir` + `write_text`. An unwritable or non-existent `VID_INDEX_DIR` -- a
    read-only mount, a typo in the variable, a full disk -- therefore escaped
    as a raw `OSError` traceback from whichever of the two happened to run,
    naming no remedy and pointing at no setting.

    `VID_INDEX_DIR` is the thing to name: it is the only reason the location is
    ever surprising, and it is the only knob the caller has.
    """
    path = index_path(video)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except OSError as exc:
        raise VidError(
            f"The index could not be written to {path}: {exc.strerror or exc}.\n"
            "That location comes from VID_INDEX_DIR when it is set, and a cache "
            "directory beside the video otherwise. Point VID_INDEX_DIR at a "
            "writable directory, or free space on this one."
        ) from exc
    return path


def chunks_of(record: dict) -> list[Chunk]:
    return [Chunk(**chunk) for chunk in record.get("speech", [])]
