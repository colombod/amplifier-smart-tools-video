"""Describing frames must fail loud, not silently drop what it could not do.

Every shot handed to `extract_frames` or `describe_shots` must come back
described, or the caller gets a named list of what failed -- never a quiet
subset that looks, to the record, identical to "never attempted".

`describe_shots` calls a model through the `Intelligence` interface, and there
is no way to reach a real one in a test that must run offline and
deterministically. So these tests use a fake that implements the same `.run`
contract by actually reading the prompt vision.py sends -- the same pattern
`tests/test_narrate.py` already uses for its `Shortener`/`Stubborn` fakes --
rather than a canned reply standing in for a described outcome.
"""

from __future__ import annotations

import pytest

from tests.fixtures import ensure_clips, have_ffmpeg
from vid.schemas import VidError
from vid.vision import describe_shots, extract_frames

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="these extract real frames")


class RespondingIntelligence:
    """Answers by actually reading the frame listing vision.py sent, and
    describing every frame except any shot id named in `skip`.

    This is not a canned reply pretending to be a model -- it parses the real
    prompt structure (`"<file> -- the shot at <shot_id>"`) and produces a
    reply in the exact format `describe_shots` expects, the same way a real
    model would after reading the same directory listing.
    """

    def __init__(self, skip: frozenset[str] = frozenset()) -> None:
        self.skip = skip

    def run(self, request):
        lines = []
        for line in request.prompt.splitlines():
            if " -- the shot at " not in line:
                continue
            name, _, shot_id = line.partition(" -- the shot at ")
            shot_id = shot_id.strip()
            if shot_id in self.skip:
                continue
            lines.append(f"{name}: a plain, uneventful frame.")
        return type("Result", (), {"text": "\n".join(lines), "error": None})()


def _shots(*pairs: tuple[str, float, float]) -> list[dict]:
    return [{"id": shot_id, "start": start, "end": end} for shot_id, start, end in pairs]


def test_extract_frames_fails_loud_and_names_the_bad_shot(tmp_path):
    """One shot pointed well past the clip's own duration must fail the whole
    extraction, and the failure must name which shot and why -- not silently
    return the frames that did work."""
    video = str(ensure_clips()["alpha"].path)  # a real 3.0s clip
    shots = _shots(("s0", 0.5, 1.5), ("s1", 500.0, 501.0))

    with pytest.raises(VidError) as failure:
        extract_frames(video, shots, tmp_path / "frames")

    message = str(failure.value)
    assert "s1" in message, "the failing shot must be named"
    assert "1 of 2" in message, "the caller needs to know how many of how many failed"


def test_extract_frames_succeeds_when_every_shot_is_real(tmp_path):
    video = str(ensure_clips()["alpha"].path)
    shots = _shots(("s0", 0.5, 1.0), ("s1", 1.5, 2.0))

    written = extract_frames(video, shots, tmp_path / "frames")

    assert {shot_id for shot_id, _ in written} == {"s0", "s1"}


def test_describe_shots_fails_loud_when_the_model_skips_a_frame(tmp_path):
    """The model answering about only some of the frames it was shown must
    not be accepted as a valid, if partial, result."""
    video = str(ensure_clips()["alpha"].path)
    shots = _shots(("s0", 0.5, 1.0), ("s1", 1.5, 2.0))

    with pytest.raises(VidError) as failure:
        describe_shots(video, shots, RespondingIntelligence(skip=frozenset({"s1"})))

    message = str(failure.value)
    assert "s1" in message, "the omitted shot must be named"
    assert "1 of 2" in message


def test_describe_shots_returns_every_shot_when_the_model_answers_for_all(tmp_path):
    video = str(ensure_clips()["alpha"].path)
    shots = _shots(("s0", 0.5, 1.0), ("s1", 1.5, 2.0))

    described = describe_shots(video, shots, RespondingIntelligence())

    assert {item.shot_id for item in described} == {"s0", "s1"}
    assert all(item.description for item in described)
