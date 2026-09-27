"""Describing what is SHOWN, so a silent video stops being invisible.

WHY THIS IS AFFORDABLE AT ALL, and it was settled before any code existed: shot
detection runs first, deterministically and for free. A ten-minute recording is
around eighteen thousand frames, and describing eighteen thousand frames is
absurd at any price. Shot boundaries take that to a few dozen -- one
representative frame each -- and describing forty frames costs cents.

The cheap step is what makes the expensive one viable. That is why shot detection
shipped first.

THE GUARD IS UNCHANGED. A model is never asked WHEN something happened. Each shot
already carries a time range that ffmpeg's scene detection produced; a model
describes a frame, and the timestamp stays a dictionary lookup. Adding vision may
not soften that, because a plausible invented timestamp is indistinguishable from
a correct one until somebody watches the video.

HOW THE IMAGE REACHES THE MODEL. `AgentRequest` has no image field -- it has a
`workspace`, and the agent behind it has file-reading tools. So frames are
written to a directory and the agent is pointed at it. Verified before building
on it: a frame carrying the text "INVOICE 4471" came back described as exactly
that.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile

from vid.core.writes import writing
from vid.schemas import DEFAULT_INTELLIGENCE_MODEL, ReasoningEffort, VidError

#: Frames are described at this width. A description does not get better from
#: more pixels than a person would need, and every extra pixel is paid for.
FRAME_WIDTH = 768


@dataclass(frozen=True)
class Described:
    shot_id: str
    description: str


def _representative_moment(start: float, end: float) -> float:
    """The MIDPOINT of a shot, not its first frame.

    A shot's first frame is often a transition still in progress -- half of the
    outgoing clip, mid-dissolve -- which describes the edit rather than the
    content. The midpoint is the shot actually being itself.
    """
    return start + (end - start) / 2.0


def extract_frames(video: str, shots: list[dict], into: Path) -> list[tuple[str, Path]]:
    """One frame per shot, written where an agent can read them.

    EVERY REQUESTED SHOT OR NONE. A frame that failed to extract used to be
    silently dropped, and the caller had no way to tell a shot that was never
    attempted from one ffmpeg genuinely could not read -- both looked exactly
    like "not in the result". Refusing the whole batch when even one shot
    fails means a caller always gets either a complete set of frames or a
    named list of what went wrong; there is no third, ambiguous outcome.
    """
    with writing(into, "The extracted frames", "Choose a writable location, or free space on this one."):
        into.mkdir(parents=True, exist_ok=True)
    written: list[tuple[str, Path]] = []
    failed: list[str] = []
    for position, shot in enumerate(shots):
        at = _representative_moment(shot["start"], shot["end"])
        out = into / f"shot{position:03d}.png"
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                str(at),
                "-i",
                video,
                "-frames:v",
                "1",
                "-vf",
                f"scale={FRAME_WIDTH}:-2",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and out.is_file():
            written.append((shot["id"], out))
        else:
            reason = (result.stderr or "").strip().splitlines()
            failed.append(f"{shot['id']} (at {at:.2f}s): {reason[-1] if reason else 'ffmpeg gave no reason'}")
    if failed:
        raise VidError(
            f"Could not extract a frame for {len(failed)} of {len(shots)} shot(s) from {video!r}:\n  "
            + "\n  ".join(failed)
            + "\nDescribing needs a frame from every shot asked about, so none were applied. "
            "Verify the file with `ffprobe`, or try `vid check`."
        )
    if not written:
        raise VidError(
            f"Could not extract a single frame from {video!r}. "
            "Is it a video file, and does ffmpeg read it? Try `vid check`."
        )
    return written


def describe_shots(
    video: str,
    shots: list[dict],
    intelligence,
    model: str = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: ReasoningEffort = "low",
) -> list[Described]:
    """Describe one frame per shot, in a single pass.

    One call for the whole set rather than one per frame: the agent reads the
    directory, and a batch keeps the cost proportional to the video rather than
    to a round trip per shot.
    """
    from vid.intelligence.schemas import AgentRequest, HostWorkspace
    from vid.schemas import VidError as _VidError

    with tempfile.TemporaryDirectory(prefix="vid-vision-") as work:
        workspace = Path(work)
        frames = extract_frames(video, shots, workspace)
        listing = "\n".join(f"{path.name} -- the shot at {shot_id}" for shot_id, path in frames)

        result = intelligence.run(
            AgentRequest(
                prompt=(
                    "This directory contains one still frame from each shot of a video.\n\n"
                    f"{listing}\n\n"
                    "Look at every one of these image files and describe what it SHOWS, so "
                    "that someone searching the video later could find this moment by "
                    "describing it from memory.\n\n"
                    "For each, reply with one line: the filename, a colon, then the "
                    "description. Mention what is on screen, any visible text or UI, and "
                    "what appears to be happening. Two sentences at most. Describe only "
                    "what you can actually see -- do not guess at what came before or after, "
                    "and do not invent detail that is not in the picture.\n"
                    "Nothing but those lines."
                ),
                model=model,
                reasoning_effort=reasoning_effort,
                workspace=HostWorkspace(path=workspace),
                timeout_seconds=90 + 15 * len(frames),
            )
        )
        if getattr(result, "error", None):
            raise _VidError(
                f"The model could not describe the frames: {result.error}\n"
                "Check that `gh auth login` is signed in with an account that has a Copilot "
                "subscription, or run `vid check` to see what this machine has configured."
            )

        by_name = {path.name: shot_id for shot_id, path in frames}
        described: list[Described] = []
        seen_ids: set[str] = set()
        for raw in (result.text or "").splitlines():
            name, _, text = raw.partition(":")
            name = name.strip().strip("`*-. ")
            shot_id = by_name.get(name)
            if shot_id and text.strip():
                described.append(Described(shot_id=shot_id, description=text.strip()))
                seen_ids.add(shot_id)

        if not described:
            raise _VidError(
                f"The model returned no usable descriptions. It said: {(result.text or '')[:200]!r} "
                "Retry, or re-run `vid index --vision`."
            )

        # EVERY FRAME OR NONE APPLIED. Accepting whatever nonempty subset came
        # back left a shot the model skipped indistinguishable from one nobody
        # ever asked about -- both had no "description" key. Refusing the
        # whole batch instead means a caller only ever sees a fully described
        # set, and a partial reply is a named failure rather than a silent gap.
        missing = [shot_id for shot_id, _ in frames if shot_id not in seen_ids]
        if missing:
            raise _VidError(
                f"The model described {len(described)} of {len(frames)} frame(s) but said nothing "
                f"usable about: {', '.join(missing)}.\n"
                f"Its reply:\n{(result.text or '')[:500]!r}\n"
                "None were applied -- re-run `vid index --vision` to try again."
            )
        return described
