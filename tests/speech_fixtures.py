"""A video with speech whose content and timing we AUTHORED.

`find "where she explains pricing"` can only be tested against a video where we
already know when pricing is discussed. Downloading a real talk gives realistic
speech and no ground truth; we would be reduced to eyeballing the answer, which
is the thing this tool exists to stop people doing.

So the script is written here, spoken by a TTS, and assembled with the segment
boundaries recorded beside it. A test asserts against that record, not against a
human's memory of the clip.

WHAT THIS IS NOT. espeak-ng is robotic. Clean, clearly-articulated robotic speech
is EASIER to transcribe than a real person in a real room, so a `find` that works
here is not thereby proven to work on a conference recording. This fixture tests
the plumbing -- transcript to chunks to a matched time range -- and deliberately
does not claim to measure real-world transcription quality.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SPEECH_VIDEO = FIXTURE_DIR / "talk.mp4"
GROUND_TRUTH = FIXTURE_DIR / "talk.groundtruth.json"

#: The script. Each entry is one topic, spoken in one stretch, and the assembled
#: video plays them in this order. Topics are deliberately unlike each other, so
#: a search that returns the wrong one is unambiguously wrong rather than
#: arguably close.
SCRIPT: list[tuple[str, str]] = [
    (
        "architecture",
        "Welcome to the product demo. First, let us look at how the system is put "
        "together. There is a web front end, a message queue, and two background workers.",
    ),
    (
        "pricing",
        "Now let us talk about what it costs. The starter plan is ten dollars per month "
        "for a single user. The team plan is forty dollars per month and covers ten seats.",
    ),
    (
        "support",
        "Finally, a word about support. Customers on any plan can open a ticket, and we "
        "answer within one business day.",
    ),
]


def have_tts() -> bool:
    return shutil.which("espeak-ng") is not None or shutil.which("espeak") is not None


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@dataclass(frozen=True)
class Segment:
    topic: str
    text: str
    start: float
    end: float

    def contains(self, seconds: float) -> bool:
        return self.start <= seconds <= self.end


def _speak(text: str, wav: Path) -> None:
    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    subprocess.run(
        # -s 150 is close to an unhurried speaking pace; the default gabbles and
        # transcribes worse, which would test the TTS rather than the tool.
        [binary, "-s", "150", "-w", str(wav), text],
        check=True,
        capture_output=True,
    )


def _duration(path: Path) -> float:
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


def ensure_talk() -> tuple[Path, list[Segment]]:
    """Build the talk if missing; return it with the authored segment boundaries."""
    if not (have_tts() and have_ffmpeg()):
        raise RuntimeError("espeak-ng and ffmpeg must both be on PATH to build speech fixtures")

    if SPEECH_VIDEO.exists() and GROUND_TRUTH.exists():
        data = json.loads(GROUND_TRUTH.read_text())
        return SPEECH_VIDEO, [Segment(**segment) for segment in data["segments"]]

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    work = FIXTURE_DIR / "_speech"
    work.mkdir(exist_ok=True)

    segments: list[Segment] = []
    parts: list[Path] = []
    cursor = 0.0
    for index, (topic, text) in enumerate(SCRIPT):
        wav = work / f"{index}.wav"
        _speak(text, wav)
        length = _duration(wav)
        segments.append(Segment(topic=topic, text=text, start=cursor, end=cursor + length))
        cursor += length
        parts.append(wav)

    listing = work / "list.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
    joined = work / "all.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)],
        check=True,
        capture_output=True,
        cwd=work,
    )

    # A plain colour track: the picture is irrelevant here, and a cheap one keeps
    # the fixture fast to build.
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=gray:s=640x360:r=15:d={cursor}",
            "-i",
            str(joined),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(SPEECH_VIDEO),
        ],
        check=True,
        capture_output=True,
    )

    GROUND_TRUTH.write_text(
        json.dumps(
            {"total_seconds": cursor, "segments": [segment.__dict__ for segment in segments]},
            indent=2,
        )
    )
    shutil.rmtree(work, ignore_errors=True)
    return SPEECH_VIDEO, segments


def segment_for(topic: str, segments: list[Segment]) -> Segment:
    for segment in segments:
        if segment.topic == topic:
            return segment
    raise KeyError(topic)


# ---------------------------------------------------------------------------
# A MEETING-SHAPED FIXTURE
#
# The simple talk above is one voice reading three clean paragraphs. Checking
# `find` against a real 52-minute two-person meeting showed what that misses --
# and the gap was not audio quality, it was CONVERSATIONAL SHAPE:
#
#   two speakers        turns interrupt each other mid-sentence
#   backchannel         "yeah", "mhm" -- passages carrying no searchable content
#   false starts        a word repeated, then abandoned
#   topic drift         a subject mentioned in passing long before it is discussed
#   the paraphrase gap  the searcher's word is not the speaker's word
#
# That last one matters most, and the real meeting is what surfaced it. Three
# plausible queries against 52 minutes were ALL answered by the literal tier,
# because people searching a recording tend to remember words rather than
# paraphrases. So the described tier -- the one that needs a model -- was never
# exercised on real speech at all.
#
# This fixture forces it. The "spend" topic is discussed at length WITHOUT the
# words a searcher would reach for, so a query like "budget" or "cost" cannot
# match literally and must escalate.
#
# The real recording stays out of this repository: it is private, and it is not
# ours to publish. It shaped this fixture and then went back in its box.
# ---------------------------------------------------------------------------

MEETING_VIDEO = FIXTURE_DIR / "meeting.mp4"
MEETING_TRUTH = FIXTURE_DIR / "meeting.groundtruth.json"

#: (voice, topic, line). `topic` is None for backchannel and filler -- passages
#: that exist to make the transcript realistic and must never be an answer.
MEETING: list[tuple[str, str | None, str]] = [
    ("a", "rollout", "So the plan is to deploy the new release to the staging cluster on Tuesday."),
    ("b", None, "Right, yeah."),
    ("a", "rollout", "We deploy to staging first, then production on Thursday if nothing breaks."),
    ("b", "rollout", "And the rollback? If the deploy goes wrong on Thursday, what happens?"),
    ("a", "rollout", "We keep the previous release running, so a rollback is just a switch."),
    ("b", None, "Mhm. OK."),
    # The paraphrase trap. This is the money topic and it never says money,
    # cost, budget, spend or price. A literal search for any of those fails.
    ("a", "money", "The other thing is, well, the other thing is what we are paying every month."),
    ("b", None, "Go on."),
    ("a", "money", "The bill from the provider has gone up three times since January."),
    ("b", "money", "Three times? That is eating straight into our margin."),
    ("a", "money", "It is. If it keeps climbing we cannot keep the current plan for the team."),
    ("b", "money", "So we either move provider or we make the workers less hungry."),
    ("b", None, "Yeah. Yeah."),
    # Drift trap: "deploy" appears here, far from the rollout discussion, so a
    # naive literal search finds two runs and has to rank them.
    ("a", "hiring", "Last thing. We should hire another engineer before the next deploy cycle."),
    ("b", "hiring", "Agreed. Someone senior, who has run a platform team before."),
    ("a", "hiring", "I will write the job description this week and send it round."),
]

#: espeak voice names, chosen to be clearly distinguishable.
_VOICES = {"a": "en+m3", "b": "en+f2"}


@dataclass(frozen=True)
class Turn:
    speaker: str
    topic: str | None
    text: str
    start: float
    end: float


def _speak_as(voice: str, text: str, wav: Path) -> None:
    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    subprocess.run(
        [binary, "-v", voice, "-s", "150", "-w", str(wav), text],
        check=True,
        capture_output=True,
    )


def ensure_meeting() -> tuple[Path, list[Turn]]:
    """Build the two-speaker meeting if missing; return it with its turn map."""
    if not (have_tts() and have_ffmpeg()):
        raise RuntimeError("espeak-ng and ffmpeg must both be on PATH to build speech fixtures")

    if MEETING_VIDEO.exists() and MEETING_TRUTH.exists():
        data = json.loads(MEETING_TRUTH.read_text())
        return MEETING_VIDEO, [Turn(**turn) for turn in data["turns"]]

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    work = FIXTURE_DIR / "_meeting"
    work.mkdir(exist_ok=True)

    turns: list[Turn] = []
    parts: list[Path] = []
    cursor = 0.0
    for index, (speaker, topic, line) in enumerate(MEETING):
        wav = work / f"{index}.wav"
        _speak_as(_VOICES[speaker], line, wav)
        length = _duration(wav)
        turns.append(Turn(speaker=speaker, topic=topic, text=line, start=cursor, end=cursor + length))
        cursor += length
        parts.append(wav)

    listing = work / "list.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
    joined = work / "all.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)],
        check=True,
        capture_output=True,
        cwd=work,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=slategray:s=640x360:r=15:d={cursor}",
            "-i",
            str(joined),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(MEETING_VIDEO),
        ],
        check=True,
        capture_output=True,
    )

    MEETING_TRUTH.write_text(
        json.dumps({"total_seconds": cursor, "turns": [turn.__dict__ for turn in turns]}, indent=2)
    )
    shutil.rmtree(work, ignore_errors=True)
    return MEETING_VIDEO, turns


def span_of(topic: str, turns: list[Turn]) -> tuple[float, float]:
    """The full time range a topic occupies, ignoring backchannel between turns."""
    matching = [turn for turn in turns if turn.topic == topic]
    if not matching:
        raise KeyError(topic)
    return matching[0].start, matching[-1].end
