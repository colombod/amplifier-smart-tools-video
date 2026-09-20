"""The fit loop -- the part of narration that is actually the feature.

Generation is the easy half. The half that decides whether narration is usable is
what happens when a spoken line does NOT fit its slot, and that path is the one a
live run is least likely to exercise: a model told a budget usually respects it.

So the speaker here is a fake with controlled durations. That is not a shortcut
around testing the real thing -- it is the only way to reach the branch on
purpose, every time, in under a second.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures import have_ffmpeg
from vid.narrate import MAX_RATE, MIN_SLOT, REWRITE_ATTEMPTS, Line, Script, fit, slots_from
from vid.schemas import VidError


class FakeSpeaker:
    """Speaks in a straight line: duration is proportional to length.

    `seconds_per_char` is the dial a test turns to force an overrun.
    """

    def __init__(self, seconds_per_char: float = 0.1) -> None:
        self.seconds_per_char = seconds_per_char
        self.calls: list[tuple[str, float]] = []

    def say(self, text: str, out: Path | str, *, rate: float = 1.0) -> float:
        self.calls.append((text, rate))
        Path(out).write_bytes(b"")
        return len(text) * self.seconds_per_char / rate


class Shortener:
    """A model that genuinely shortens, by a fixed fraction each time."""

    def __init__(self, keep: float = 0.5) -> None:
        self.keep = keep
        self.calls = 0

    def run(self, request):
        self.calls += 1
        prompt = getattr(request, "prompt", "")
        line = prompt.split("LINE:", 1)[1].split("\n", 1)[0].strip()
        return type("R", (), {"text": line[: max(1, int(len(line) * self.keep))], "error": None})()


class Stubborn:
    """A model that will not shorten anything. The worst realistic case."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, request):
        self.calls += 1
        prompt = getattr(request, "prompt", "")
        line = prompt.split("LINE:", 1)[1].split("\n", 1)[0].strip()
        return type("R", (), {"text": line, "error": None})()


def _script(text: str, budget: float) -> Script:
    return Script(source="x.mp4", prompt="p", lines=[Line(index=0, start=0.0, budget=budget, text=text)])


def test_a_line_that_already_fits_is_left_alone(tmp_path):
    speaker = FakeSpeaker(0.1)
    model = Shortener()
    script = fit(_script("x" * 20, budget=5.0), speaker, tmp_path, model)

    assert script.lines[0].fitted
    assert model.calls == 0, "a fitting line was sent for rewriting anyway"
    assert len(speaker.calls) == 1, "a fitting line was spoken more than once"


def test_an_overrunning_line_is_rewritten_until_it_fits(tmp_path):
    """40 chars at 0.1s/char is 4.0s against a 2.5s slot. One halving fixes it."""
    speaker = FakeSpeaker(0.1)
    model = Shortener(keep=0.5)
    script = fit(_script("x" * 40, budget=2.5), speaker, tmp_path, model)

    line = script.lines[0]
    assert line.fitted, f"still {line.overrun:.2f}s over"
    assert line.attempts == 1
    assert len(line.text) == 20


def test_a_model_that_returns_the_same_line_is_asked_only_once(tmp_path):
    """Stop early rather than spend the whole budget on a refusal.

    THIS TEST WAS WRONG FIRST TIME, and the code was right. It asserted the
    stubborn model would be asked REWRITE_ATTEMPTS times -- but a rewrite that
    comes back byte-identical will not come back different on a second identical
    prompt, so the loop breaks. The cap still exists for the case below, where a
    model genuinely shortens but never quite enough.
    """
    model = Stubborn()
    fit(_script("x" * 100, budget=1.0), FakeSpeaker(0.1), tmp_path, model)

    assert model.calls == 1, f"asked {model.calls} times for a rewrite it already refused"


def test_the_attempt_cap_bounds_a_model_that_shortens_too_slowly(tmp_path):
    """Where REWRITE_ATTEMPTS actually bites.

    Trimming 5% per attempt is progress, so the identical-reply break never
    fires -- without a cap this would loop until the line was one character.
    """
    model = Shortener(keep=0.95)
    fit(_script("x" * 200, budget=1.0), FakeSpeaker(0.1), tmp_path, model)

    assert model.calls == REWRITE_ATTEMPTS, f"asked {model.calls} times with a limit of {REWRITE_ATTEMPTS}"


def test_a_small_overrun_is_absorbed_by_a_modest_speed_up(tmp_path):
    """When words cannot come down far enough, a little rate is allowed.

    21 chars is 2.1s against 2.0s -- five percent over, which is a nudge rather
    than a rewrite.
    """
    speaker = FakeSpeaker(0.1)
    script = fit(_script("x" * 21, budget=2.0), speaker, tmp_path, Stubborn())

    line = script.lines[0]
    assert line.fitted
    assert 1.0 < line.rate <= MAX_RATE, f"rate {line.rate} outside the allowed band"


def test_a_line_that_cannot_fit_is_reported_by_name_not_hidden(tmp_path):
    """The honest failure. It must not be sped up past listenable, and it must
    not silently overrun -- the caller needs to know WHICH line and by how much."""
    speaker = FakeSpeaker(0.1)
    script = fit(_script("x" * 100, budget=1.0), speaker, tmp_path, Stubborn())

    line = script.lines[0]
    assert not line.fitted
    assert line.rate <= MAX_RATE, "sped up past the listenable limit to force a fit"
    assert "over" in line.note, line.note
    assert f"{line.overrun:.2f}s over" in line.note, "the note must carry the measurement, not just the fact of failure"
    assert script.unfitted() == [line]


def test_one_bad_line_does_not_spoil_the_others(tmp_path):
    """Graceful degradation is the whole reason for fitting per segment."""
    script = Script(
        source="x.mp4",
        prompt="p",
        lines=[
            Line(index=0, start=0.0, budget=5.0, text="x" * 20),
            Line(index=1, start=5.0, budget=1.0, text="x" * 100),
            Line(index=2, start=6.0, budget=5.0, text="x" * 20),
        ],
    )
    fit(script, FakeSpeaker(0.1), tmp_path, Stubborn())

    assert [line.fitted for line in script.lines] == [True, False, True]
    assert len(script.unfitted()) == 1


def test_a_slot_the_model_left_silent_is_not_spoken(tmp_path):
    speaker = FakeSpeaker(0.1)
    script = fit(_script("", budget=5.0), speaker, tmp_path, Shortener())

    assert script.lines[0].fitted
    assert script.lines[0].note == "left silent"
    assert speaker.calls == [], "an empty line was sent to the synthesiser"


def test_without_a_model_a_long_line_is_reported_rather_than_rewritten(tmp_path):
    """Narration needs a model to WRITE, but fitting must not crash without one."""
    script = fit(_script("x" * 100, budget=1.0), FakeSpeaker(0.1), tmp_path, None)
    assert not script.lines[0].fitted


def test_short_slots_are_dropped_before_anything_is_written():
    """A one-second shot cannot hold a sentence, and cramming one in produces the
    rushed voiceover everybody recognises."""
    record = {
        "duration": 10.0,
        "shots": [
            {"id": "s0", "start": 0.0, "end": 0.5},
            {"id": "s1", "start": 0.5, "end": 6.0},
            {"id": "s2", "start": 6.0, "end": 10.0},
        ],
    }
    slots = slots_from(record)
    assert len(slots) == 2
    assert all(length >= MIN_SLOT for _, length in slots)


def test_a_video_with_no_shots_becomes_one_slot():
    """Exactly what a static screen recording looks like."""
    assert slots_from({"duration": 30.0, "shots": []}) == [(0.0, 30.0)]


def test_a_video_too_short_to_narrate_says_so():
    with pytest.raises(VidError, match="too short to narrate"):
        slots_from({"duration": 1.0, "shots": []})


@pytest.mark.skipif(not have_ffmpeg(), reason="assemble shells out to real ffmpeg")
def test_assemble_failure_names_the_remedy_not_just_the_raw_stderr(tmp_path):
    """A real ffmpeg failure -- an audio file that does not exist -- must come
    back with something a caller can act on, matching the convention every
    other ffmpeg failure in this tool already uses."""
    script = Script(
        source="x.mp4",
        prompt="p",
        lines=[Line(index=0, start=0.0, budget=5.0, text="hello", audio=tmp_path / "missing.wav")],
    )

    from vid.narrate import assemble

    with pytest.raises(VidError) as failure:
        assemble(script, tmp_path / "out.wav", total=5.0)

    message = str(failure.value)
    assert "Could not assemble the narration" in message
    assert "vid check" in message, "the failure must name a remedy, not only report the raw stderr"


def test_the_report_shows_the_measurement_beside_the_budget():
    """A caller cannot act on 'it did not fit' without knowing by how much."""
    line = Line(index=0, start=3.0, budget=2.0, text="hello", spoken=4.25, fitted=False)
    rendered = line.report()
    assert "4.25" in rendered, "the report omits what was MEASURED"
    assert "2.00" in rendered, "the report omits the BUDGET it was measured against"
    assert "OVER" in rendered, "the report does not say it failed to fit"
