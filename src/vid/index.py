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
import json
from pathlib import Path
import subprocess

from vid.schemas import VidError

INDEX_FORMAT = 1


def index_dir() -> Path:
    """Where indexes accumulate. Shared on purpose: the point is reuse."""
    import os

    override = os.environ.get("VID_INDEX_DIR")
    if override:
        return Path(override)
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
    if not path.is_file():
        raise VidError(f"No such video: {video!r}")
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(1 << 20))
        if size > (1 << 21):
            handle.seek(-(1 << 20), 2)
            digest.update(handle.read(1 << 20))
    return digest.hexdigest()[:16]


def index_path(video: str) -> Path:
    return index_dir() / f"{fingerprint(video)}.json"


def load(video: str) -> dict | None:
    path = index_path(video)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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
    result = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", video, "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    times = [0.0]
    for line in result.stderr.splitlines():
        if "pts_time:" in line:
            for token in line.split():
                if token.startswith("pts_time:"):
                    times.append(float(token.split(":")[1]))
    total = _duration(video)
    times.append(total)
    times = sorted(set(round(t, 3) for t in times if 0 <= t <= total))
    return [Shot(id=f"s{i}", start=a, end=b) for i, (a, b) in enumerate(zip(times, times[1:], strict=False))]


def _duration(video: str) -> float:
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
        raise VidError(f"{video!r} has no readable duration -- is it a video file?") from exc


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

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(video, beam_size=1)
    return [
        Chunk(id=f"c{i}", start=round(segment.start, 3), end=round(segment.end, 3), text=segment.text.strip())
        for i, segment in enumerate(segments)
    ]


def describe(video: str, record: dict, intelligence) -> dict:
    """Add a description to every shot that does not have one.

    Descriptions live ON the shots, beside time ranges ffmpeg already produced.
    A model never supplies a time -- that is the whole point of describing frames
    rather than asking when something happened.
    """
    from vid.vision import describe_shots

    shots = record.get("shots") or []
    pending = [shot for shot in shots if not shot.get("description")]
    if not pending:
        return record

    described = describe_shots(video, pending, intelligence)
    by_id = {item.shot_id: item.description for item in described}
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

    path = index_path(video)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def chunks_of(record: dict) -> list[Chunk]:
    return [Chunk(**chunk) for chunk in record.get("speech", [])]
