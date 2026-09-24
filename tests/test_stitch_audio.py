"""Stitching when the sound is missing, on either side of the join.

Every test here is about a state the PLAN cannot see. Whether a file carries an
audio stream is a property of the file, so a plan that stitches a silent clip
validates cleanly, compiles cleanly, and is then refused by ffmpeg. That is the
worst shape a failure can take: nothing on the way in says anything is wrong.

`cut()` already learned this lesson and guards it (compile.py's `a=0` branch).
`stitch()` did not, so these are written against stitch specifically.
"""

import subprocess

import pytest

from tests.fixtures import ensure_clips, ensure_silent_clip, has_audio_stream, have_ffmpeg, probe_duration
from vid.compile import compile_plan
from vid.plan import AudioRemove, Plan, Stitch
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg is required")


@pytest.fixture(scope="module")
def clips():
    return ensure_clips()


@pytest.fixture(scope="module")
def silent():
    return ensure_silent_clip()


def _graph(command: list[str]) -> str:
    """The -filter_complex argument, or an empty string when there is none."""
    return command[command.index("-filter_complex") + 1] if "-filter_complex" in command else ""


def test_silent_source_stitch_never_writes_none_into_the_graph(clips, tmp_path):
    """The literal text `None` in a filter graph is the whole bug.

    `self.audio` is None when the source carries no sound, and an f-string
    renders that as the four characters N-o-n-e. Python is happy, the compile
    is happy, and ffmpeg rejects the command.
    """
    plan = Plan(source="silent.mp4").with_operation(Stitch(sources=["b.mp4"]))
    command = compile_plan(plan, str(tmp_path / "out.mp4"), has_audio=False)

    assert "None" not in _graph(command), f"graph carries a None label: {_graph(command)}"
    assert "[None]" not in " ".join(command)


def test_audio_remove_then_stitch_renders_and_has_no_sound(clips, tmp_path):
    """`audio remove` sets the same sentinel a silent source does.

    Rendered rather than inspected: a graph can look right and still be refused,
    which is exactly how this class of defect survives a green suite.
    """
    from vid.lib import render

    plan = (
        Plan(source=str(clips["alpha"].path))
        .with_operation(AudioRemove())
        .with_operation(Stitch(sources=[str(clips["bravo"].path)]))
    )
    output = render(plan, str(tmp_path / "removed_then_stitched.mp4"))

    assert not has_audio_stream(output), "expected no audio stream after `audio remove`"
    assert probe_duration(output) == pytest.approx(clips["alpha"].seconds + clips["bravo"].seconds, abs=0.15), (
        "stitching after `audio remove` changed the duration"
    )


def test_stitching_a_silent_clip_onto_a_sounded_one_names_the_file(clips, silent, tmp_path):
    """A mismatch is refused BY NAME, not passed to ffmpeg to fail obscurely.

    The base has sound and the incoming clip has none. concat cannot take a
    stream that does not exist, and `Stream specifier ... matches no streams`
    tells a caller nothing about which of their files was the problem.
    """
    from vid.lib import render

    plan = Plan(source=str(clips["alpha"].path)).with_operation(Stitch(sources=[str(silent.path)]))

    with pytest.raises(VidError) as failure:
        render(plan, str(tmp_path / "mismatch.mp4"))

    message = str(failure.value)
    assert "silent.mp4" in message, f"the failure does not name the offending file: {message}"
    assert "audio" in message.lower(), f"the failure does not say what is missing: {message}"


def test_two_silent_clips_stitch_cleanly_into_a_silent_result(silent, tmp_path):
    """Neither side has sound, so there is no mismatch and nothing to refuse."""
    from vid.lib import render

    plan = Plan(source=str(silent.path)).with_operation(Stitch(sources=[str(silent.path)]))
    output = render(plan, str(tmp_path / "both_silent.mp4"))

    assert not has_audio_stream(output)
    assert probe_duration(output) == pytest.approx(silent.seconds * 2, abs=0.15)


def test_silent_stitch_command_is_accepted_by_ffmpeg(silent, tmp_path):
    """The command compiles AND runs.

    Separate from the render tests on purpose: this one asserts on ffmpeg's own
    exit status for the exact argv `vid` produced, so a graph that Python finds
    well-formed but ffmpeg refuses is caught as such.
    """
    plan = Plan(source=str(silent.path)).with_operation(Stitch(sources=[str(silent.path)]))
    command = compile_plan(plan, str(tmp_path / "accepted.mp4"), has_audio=False)

    finished = subprocess.run(command, capture_output=True, text=True)

    assert finished.returncode == 0, f"ffmpeg refused the command: {finished.stderr[-600:]}"
