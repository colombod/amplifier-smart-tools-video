"""#27: stating the sample rate, channels and bitrate of the finished edit.

WHAT WAS MISSING. Every join in an edit has to agree on a sample rate and
channel layout, and that agreement was settled entirely by the source file. A
caller delivering to a spec -- 48 kHz stereo for a broadcast hand-off, 44.1 kHz
mono for a podcast -- had no way to say so.

THE SPLIT, AND IT IS THE LOAD-BEARING DESIGN DECISION HERE. Rate and channels
go on the PLAN; bitrate goes on `render`:

  - Rate and channels are properties of the FILTER GRAPH. They decide what the
    edit sounds like and every join has to agree on them, so they belong in the
    document that describes the edit.
  - Bitrate is an ENCODER argument applied after the graph is built. Changing
    it changes nothing about the edit, only about the file. It sits beside
    `--video-codec`, which is likewise not stored in the plan.

Putting all three on one flag would have mixed a fact about the edit with a
fact about the file, which ffmpeg itself keeps apart.

WHY `audio format` IS NOT AN OPERATION. It does not happen at a point in the
chain the way a trim does. Stating it twice settles it once rather than
stacking two conflicting steps.

PLAN FORMAT STAYS AT 1. The field defaults to None and a plan written before it
existed renders identically -- the same reasoning #20 used for `Stitch.fit`.
`test_an_unset_format_keeps_the_sources_own` is that promise, asserted against
a real render rather than asserted in prose.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from tests.fixtures import audio_stream_format, ensure_44k_mono_clip, ensure_silent_640_clip, have_ffmpeg

REPO = Path(__file__).resolve().parents[1]
VID = str(REPO / ".venv" / "bin" / "vid")


def _run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([VID, *args], input=stdin, capture_output=True, text=True)


def _render(plan: dict, out: Path) -> None:
    result = _run(["render", str(out)], stdin=json.dumps(plan))
    assert result.returncode == 0, result.stderr


def _plan_with_format(source: str, rate: int, channels: str) -> dict:
    """Build the plan THROUGH THE CLI, not by hand.

    Hand-writing the JSON would test the compiler against a shape this tool
    might never actually emit. Driving the verb means the field name, the
    channel-word mapping and the plan it produces are all under test too.
    """
    result = _run(["audio", "format", source, "--rate", str(rate), "--channels", channels])
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# ---------------------------------------------------------------- plan shape


def test_audio_format_is_a_field_not_an_operation() -> None:
    """Stating it twice must settle it once, not stack two conflicting steps."""
    once = _plan_with_format("talk.mp4", 44100, "mono")
    assert once["audio_format"] == {"sample_rate": 44100, "channels": 1}
    assert once["operations"] == [], "audio format must not add a step to the chain"

    twice = _run(
        ["audio", "format", "--rate", "48000", "--channels", "stereo"],
        stdin=json.dumps(once),
    )
    assert twice.returncode == 0, twice.stderr
    settled = json.loads(twice.stdout)
    assert settled["audio_format"] == {"sample_rate": 48000, "channels": 2}
    assert settled["operations"] == []


def test_the_plan_format_number_does_not_move() -> None:
    """A new OPTIONAL field does not invalidate anything already written."""
    assert _plan_with_format("talk.mp4", 44100, "mono")["plan_format"] == 1


def test_audio_format_survives_later_verbs() -> None:
    """THE HAZARD `Plan.with_operation` CREATES, asserted.

    `with_operation` rebuilds the plan field by field. A plan-level field
    omitted there is not a compile error -- it silently vanishes the moment the
    caller pipes another verb after it, which is the worst kind of bug: the
    setting was accepted, echoed back, and then quietly dropped.
    """
    stated = _plan_with_format("talk.mp4", 44100, "mono")
    after = _run(["trim", "--from", "0", "--to", "1"], stdin=json.dumps(stated))
    assert after.returncode == 0, after.stderr
    carried = json.loads(after.stdout)
    assert carried["audio_format"] == {"sample_rate": 44100, "channels": 1}, (
        "a later verb dropped the stated format; check that Plan.with_operation carries every field"
    )
    assert len(carried["operations"]) == 1


# ---------------------------------------------------------------- refusals


def test_channels_takes_a_word_and_refuses_anything_it_cannot_name() -> None:
    """vid can only name mono and stereo, so it refuses rather than guessing.

    Guessing "5.1" for a 6-channel request could silently reorder a caller's
    channels, which is worse than declining the request.
    """
    result = _run(["audio", "format", "talk.mp4", "--rate", "44100", "--channels", "quad"])
    assert result.returncode != 0
    assert "mono or stereo" in result.stderr
    assert "quad" in result.stderr


def test_a_rate_outside_the_bounds_is_refused_by_the_model() -> None:
    result = _run(["audio", "format", "talk.mp4", "--rate", "999", "--channels", "mono"])
    assert result.returncode != 0
    assert "8000" in result.stderr


def test_a_malformed_bitrate_is_refused_before_ffmpeg_sees_it() -> None:
    """Named here, or it returns as a wall of encoder usage text.

    Handed straight through, "192kbps" reaches ffmpeg as an unparseable option
    and the caller's own mistake appears nowhere in the output.
    """
    plan = json.dumps({"plan_format": 1, "source": "talk.mp4", "operations": []})
    result = _run(["render", "out.mp4", "--print-command", "--audio-bitrate", "192kbps"], stdin=plan)
    assert result.returncode != 0
    assert "192kbps" in result.stderr
    assert "192k" in result.stderr, "the refusal should show the form it wants"


# ---------------------------------------------------------------- the encoder flag


def test_audio_bitrate_reaches_the_encoder_and_is_absent_when_unset() -> None:
    """Asserted on the emitted command, because that is what the flag controls.

    Checking the rendered file's measured bitrate instead would be testing the
    AAC encoder's rate control on whatever material the fixture happens to be,
    which is not what this flag promises.
    """
    plan = json.dumps(
        {"plan_format": 1, "source": "talk.mp4", "operations": [{"op": "trim", "start": "0", "end": "1"}]}
    )
    asked = _run(["render", "out.mp4", "--print-command", "--audio-bitrate", "192k"], stdin=plan)
    assert asked.returncode == 0, asked.stderr
    assert "-c:a aac -b:a 192k" in asked.stdout

    unasked = _run(["render", "out.mp4", "--print-command"], stdin=plan)
    assert unasked.returncode == 0, unasked.stderr
    assert "-b:a" not in unasked.stdout, "an unset bitrate must add nothing at all"
    assert not (REPO / "out.mp4").exists()


# ---------------------------------------------------------------- real renders


@pytest.mark.skipif(not have_ffmpeg(), reason="renders a real file")
def test_an_unset_format_keeps_the_sources_own(tmp_path: Path) -> None:
    """The backwards-compatibility promise, measured rather than asserted."""
    source = ensure_44k_mono_clip()
    out = tmp_path / "out.mp4"
    _render(
        {
            "plan_format": 1,
            "source": str(source.path),
            "operations": [{"op": "trim", "start": "0", "end": "1"}],
        },
        out,
    )
    assert audio_stream_format(out) == (44100, 1)


@pytest.mark.skipif(not have_ffmpeg(), reason="renders a real file")
def test_a_stated_format_applies_to_a_plan_with_no_join_in_it(tmp_path: Path) -> None:
    """WHY THE FINAL CONFORM EXISTS.

    `audio_join_format` already makes every JOIN agree, so a stitch would carry
    the stated format without any extra step. A bare `trim` never passes
    through that path at all. Without the end-of-graph conform, a stated format
    would apply to some plans and silently not to others depending on which
    verbs happened to be present -- exactly the surprise this field exists to
    remove.
    """
    source = ensure_44k_mono_clip()
    plan = _plan_with_format(str(source.path), 48000, "stereo")
    plan["operations"] = [{"op": "trim", "start": "0", "end": "1"}]
    out = tmp_path / "out.mp4"
    _render(plan, out)
    assert audio_stream_format(out) == (48000, 2)


@pytest.mark.skipif(not have_ffmpeg(), reason="renders a real file")
def test_a_stated_format_beats_the_probed_one(tmp_path: Path) -> None:
    """Rung 1 over rung 2: an explicit statement outranks what was measured.

    The source is 44.1 kHz mono and would be kept if nothing were stated, so a
    48 kHz stereo output can only mean the stated format won.
    """
    source = ensure_44k_mono_clip()
    silent = ensure_silent_640_clip()
    plan = _plan_with_format(str(source.path), 48000, "stereo")
    plan["operations"] = [{"op": "stitch", "sources": [str(silent.path)]}]
    out = tmp_path / "out.mp4"
    _render(plan, out)
    assert audio_stream_format(source.path) == (44100, 1)
    assert audio_stream_format(out) == (48000, 2)
