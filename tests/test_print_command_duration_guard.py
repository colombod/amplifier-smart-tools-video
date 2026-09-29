"""The duration guard: when `--print-command` may refuse, and when it may not.

RENAMED FROM `never_refuses`, WHICH WAS FALSE. A review measured plans that
still refuse in the fallback -- a stitch with a transition, an overlay
contributing sound, a zoom -- and they refuse honestly, because without a
length there is no faithful command to print. The guarantee is narrower than
the old filename claimed: supplied AUDIO (`audio replace`, `audio mix`) must
not refuse, because those are the cases #29 broke.

THE REGRESSION THIS PINS. `vid render --print-command` is a pure compile: it
exists so the graph can be inspected on a machine with no ffmpeg and no media
on disk, so it probes nothing and every duration is unknown. A guard that
refuses on an unknown duration therefore fires on EVERY plan reaching it --
and `audio replace` and `audio mix` both do.

The result was a flag whose whole job is to show you the command it would run,
failing outright on plans that render perfectly well:

    $ vid render out.mp4 --print-command   < audio_replace plan
    vid: Supplied audio needs a known positive video duration.
    $ vid render out.mp4                   < the same plan
    -> renders, 29.27s

Six merges and 721 green tests went past without noticing, because nothing
compared what `--print-command` prints against what `render` actually does.

THE GUARD ITSELF IS KEPT, and that is the other half of the contract. On a real
render an unknown duration is a genuine fault -- `trim`/`cut` once left it
unset and `audio replace` after one rendered hours of padded silence instead of
failing. So the refusal must still fire for a render and must not fire for a
pure compile, and both directions are asserted below.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from vid.compile import compile_plan
from vid.plan import AudioMix, AudioReplace, Plan, Trim
from vid.schemas import VidError

REPO = Path(__file__).resolve().parents[1]
VID = str(REPO / ".venv" / "bin" / "vid")
TALK = "tests/fixtures/talk.mp4"
MEETING = "tests/fixtures/meeting.mp4"


def _print_command(plan: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [VID, "render", "out.mp4", "--print-command"],
        input=json.dumps(plan),
        capture_output=True,
        text=True,
        cwd=REPO,
    )


@pytest.mark.parametrize(
    ("label", "operation"),
    [
        ("audio_replace", {"op": "audio_replace", "track": MEETING}),
        ("audio_mix", {"op": "audio_mix", "track": MEETING, "level": -18}),
    ],
)
def test_print_command_emits_a_command_for_supplied_audio(label: str, operation: dict) -> None:
    """THE REGRESSION. Both of these refused outright."""
    result = _print_command({"plan_format": 1, "source": TALK, "operations": [operation]})

    assert result.returncode == 0, f"--print-command refused a {label} plan: {result.stderr.strip()}"
    assert result.stdout.startswith("ffmpeg"), f"no command printed for {label}: {result.stdout!r}"
    assert "filter_complex" in result.stdout, f"no filter graph printed for {label}"
    assert not (REPO / "out.mp4").exists(), "--print-command must not render anything"


def test_the_printed_command_is_not_an_error_message() -> None:
    """A refusal on stderr with rc=0 would satisfy a naive check. This does not."""
    result = _print_command(
        {"plan_format": 1, "source": TALK, "operations": [{"op": "audio_replace", "track": MEETING}]}
    )
    assert "needs a known positive video duration" not in result.stdout + result.stderr, (
        "the duration guard is still firing on a pure compile"
    )


@pytest.mark.parametrize(
    ("label", "operation"),
    [
        ("audio_replace", AudioReplace(track=MEETING)),
        ("audio_mix", AudioMix(track=MEETING, level=-18)),
    ],
)
def test_a_real_render_still_refuses_an_unknown_duration(label: str, operation) -> None:
    """THE OTHER HALF. Losing this brings back hours of padded silence.

    Driven through `compile_plan` with no durations supplied, which is exactly
    the state the render path is in when it could not establish a length.
    """
    plan = Plan(source=TALK).with_operation(operation)
    with pytest.raises(VidError, match="known positive video duration"):
        compile_plan(plan, "out.mp4", pure_compile=False)


@pytest.mark.parametrize(
    ("label", "operation"),
    [
        ("audio_replace", AudioReplace(track=MEETING)),
        ("audio_mix", AudioMix(track=MEETING, level=-18)),
    ],
)
def test_a_pure_compile_emits_the_same_plan(label: str, operation) -> None:
    plan = Plan(source=TALK).with_operation(operation)
    command = compile_plan(plan, "out.mp4", pure_compile=True)
    assert command[0] == "ffmpeg"
    assert "-filter_complex" in command


def test_a_known_duration_still_pads_and_trims_to_the_video() -> None:
    """The working path is UNCHANGED.

    When the length IS known the graph must still carry `apad`/`atrim`, which
    is what makes supplied audio match the picture. The fix skips those only
    where the number does not exist -- it must not drop them everywhere.
    """
    plan = Plan(source=TALK).with_operation(AudioReplace(track=MEETING))
    graph = " ".join(compile_plan(plan, "out.mp4", durations={TALK: 30.0}, pure_compile=False))
    assert "apad" in graph, "supplied audio is no longer padded to the video length"
    assert "atrim=end=30.000000" in graph, f"supplied audio is no longer trimmed to the video: {graph}"


def test_trim_then_audio_replace_still_renders_and_is_bounded(tmp_path: Path) -> None:
    """The composition the guard exists to protect, end to end."""
    plan = Plan(source=TALK).with_operation(Trim(start="0", end="2")).with_operation(AudioReplace(track=MEETING))
    out = tmp_path / "out.mp4"
    result = subprocess.run(
        [VID, "render", str(out)], input=plan.model_dump_json(), capture_output=True, text=True, cwd=REPO
    )
    assert result.returncode == 0, result.stderr
    duration = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert abs(float(duration) - 2.0) < 0.35, f"trim+audio_replace rendered {duration}s, expected ~2s"
