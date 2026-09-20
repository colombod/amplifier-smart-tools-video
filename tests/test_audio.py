"""The audio verbs, measured rather than eyeballed.

The fixtures carry distinct tones -- alpha 440Hz, bravo 660Hz, charlie 880Hz --
so "is the right sound present" is a frequency measurement, not an impression. A
Goertzel filter answers it in a few lines and needs no numpy.

This matters more for audio than for video. A silent track and a missing track
look identical in a file listing; a music bed at the wrong level looks identical
to one at the right level. Only measuring tells them apart.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.fixtures import ensure_clips, has_audio_stream, have_ffmpeg, tone_strength
from vid.compile import compile_plan
from vid.plan import AudioMix, AudioRemove, AudioReplace, Plan
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="these render real audio")


def render(plan: Plan, out: str, durations: dict[str, float]) -> str:
    subprocess.run(compile_plan(plan, out, durations=durations), check=True, capture_output=True)
    return out


@pytest.fixture(scope="module")
def clips():
    built = ensure_clips()
    return {name: str(clip.path) for name, clip in built.items()}


def test_remove_produces_a_file_with_no_audio_stream_at_all(clips, tmp_path):
    """Not a silent track -- NO track. They are different files.

    A silent stream still costs bytes, still confuses a later `mix`, and still
    reports as having audio. "Remove" has to mean removed.
    """
    out = render(
        Plan(source=clips["alpha"]).with_operation(AudioRemove()),
        str(tmp_path / "silent.mp4"),
        {clips["alpha"]: 3.0},
    )
    assert not has_audio_stream(out), "remove left an audio stream behind"


def test_remove_leaves_the_picture_alone(clips, tmp_path):
    from vid.verify import _rgb_at

    out = render(
        Plan(source=clips["alpha"]).with_operation(AudioRemove()),
        str(tmp_path / "silent.mp4"),
        {clips["alpha"]: 3.0},
    )
    red, green, blue = _rgb_at(out, 1.0)
    assert red > 150, f"removing audio changed the picture: red {red}"
    assert green < 60, f"removing audio changed the picture: green {green}"
    assert blue < 60, f"removing audio changed the picture: blue {blue}"


def test_replace_swaps_the_track_entirely(clips, tmp_path):
    """alpha's video, bravo's sound. 440 must be gone and 660 must be there."""
    out = render(
        Plan(source=clips["alpha"]).with_operation(AudioReplace(track=clips["bravo"])),
        str(tmp_path / "swapped.mp4"),
        {clips["alpha"]: 3.0},
    )
    assert tone_strength(out, 660, at=1.0) > 0.8, "the replacement track is not audible"
    assert tone_strength(out, 440, at=1.0) < 0.2, "the original track survived a replace"


def test_mix_keeps_both_and_puts_the_incoming_one_under(clips, tmp_path):
    """The whole point of `mix`: both audible, one clearly below the other."""
    out = render(
        Plan(source=clips["alpha"]).with_operation(AudioMix(track=clips["bravo"], level=-18.0)),
        str(tmp_path / "mixed.mp4"),
        {clips["alpha"]: 3.0},
    )
    original = tone_strength(out, 440, at=1.0)
    bed = tone_strength(out, 660, at=1.0)
    assert original > 0.8, "the original audio was lost in the mix"
    assert bed > 0.0, "the mixed-in track is not present at all"
    assert bed < original, f"the bed ({bed:.2f}) is not below the original ({original:.2f})"


def test_a_louder_level_really_is_louder(clips, tmp_path):
    """--level has to do something monotonic, or it is decoration."""
    quiet = render(
        Plan(source=clips["alpha"]).with_operation(AudioMix(track=clips["bravo"], level=-30.0)),
        str(tmp_path / "quiet.mp4"),
        {clips["alpha"]: 3.0},
    )
    loud = render(
        Plan(source=clips["alpha"]).with_operation(AudioMix(track=clips["bravo"], level=-6.0)),
        str(tmp_path / "loud.mp4"),
        {clips["alpha"]: 3.0},
    )
    assert tone_strength(loud, 660, at=1.0) > tone_strength(quiet, 660, at=1.0)


def test_mixing_into_removed_audio_is_refused(clips):
    """There is nothing to mix into, and `replace` is what was meant."""
    plan = Plan(source=clips["alpha"]).with_operation(AudioRemove()).with_operation(AudioMix(track=clips["bravo"]))
    with pytest.raises(VidError, match="no audio left"):
        compile_plan(plan, "out.mp4", durations={clips["alpha"]: 3.0})


def test_replace_after_remove_works(clips, tmp_path):
    """The documented route for putting a track on a silent video."""
    out = render(
        Plan(source=clips["alpha"]).with_operation(AudioRemove()).with_operation(AudioReplace(track=clips["charlie"])),
        str(tmp_path / "revoiced.mp4"),
        {clips["alpha"]: 3.0},
    )
    assert has_audio_stream(out)
    assert tone_strength(out, 880, at=1.0) > 0.8


def test_a_longer_track_does_not_extend_the_video(clips, tmp_path):
    """The length rule, enforced rather than documented and hoped for.

    A long music file silently making your video longer is the kind of surprise
    that costs someone a re-export at the worst moment.
    """
    from vid.verify import duration

    long_track = str(tmp_path / "long.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=660:duration=30", long_track],
        check=True,
        capture_output=True,
    )
    out = render(
        Plan(source=clips["alpha"]).with_operation(AudioReplace(track=long_track)),
        str(tmp_path / "bounded.mp4"),
        {clips["alpha"]: 3.0},
    )
    assert duration(out) < 4.0, f"a 30s track stretched a 3s video to {duration(out):.1f}s"


def test_the_audio_verbs_need_no_provider(clips, tmp_path):
    """Stated in the manifest, so it gets a test rather than a promise."""
    command = compile_plan(
        Plan(source=clips["alpha"]).with_operation(AudioMix(track=clips["bravo"])),
        str(tmp_path / "x.mp4"),
        durations={clips["alpha"]: 3.0},
    )
    assert command[0] == "ffmpeg"


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(("trim", {"start": 1.0, "end": 2.0}), id="trim"),
        pytest.param(("cut", {"start": 1.0, "end": 2.0}), id="cut"),
        pytest.param(("retime", {"speed": 2.0}), id="retime-constant"),
        pytest.param(("retime", {"ramp": [{"at": 0.0, "speed": 1.0}, {"at": 2.0, "speed": 0.5}]}), id="retime-ramp"),
    ],
)
def test_time_verbs_survive_a_removed_audio_track(clips, tmp_path, operation):
    """A silent video can still be trimmed, cut and retimed.

    Before this was guarded, every one of these emitted the literal string
    "None" into the filter graph -- `[None]atrim=start=1.0...` -- and ffmpeg
    rejected the whole command with an error naming neither the verb nor the
    cause.

    It was unreachable until `audio remove` existed, and invisible to the type
    checker at runtime because `from __future__ import annotations` makes the
    annotation a lazy string. Only compiling the combination showed it.
    """
    from vid.plan import Cut, RampPoint, Retime, Trim

    name, kwargs = operation
    if name == "trim":
        op = Trim(**kwargs)
    elif name == "cut":
        op = Cut(**kwargs)
    elif "ramp" in kwargs:
        op = Retime(ramp=[RampPoint(**point) for point in kwargs["ramp"]])
    else:
        op = Retime(**kwargs)

    plan = Plan(source=clips["alpha"]).with_operation(AudioRemove()).with_operation(op)
    command = compile_plan(plan, str(tmp_path / "out.mp4"), durations={clips["alpha"]: 3.0})

    graph = next((part for part in command if "trim" in part or "setpts" in part), "")
    assert "None" not in graph, f"the absent audio stream leaked into the graph: {graph[:120]}"

    # And it must actually run, not merely look right.
    subprocess.run(command, check=True, capture_output=True)
    assert not has_audio_stream(str(tmp_path / "out.mp4"))
