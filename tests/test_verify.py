"""The verification checks, proved against videos we built on purpose.

THE LOAD-BEARING TEST is `test_the_blend_check_tells_a_dissolve_from_a_hard_cut`.
A check that passes on a real crossfade proves nothing on its own -- it has to
FAIL on a hard cut, or it is not measuring what it claims. Both directions, same
check, or the assertion is theatre.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tests.fixtures import ensure_clips, have_ffmpeg
from vid import verify as checks
from vid.plan import Plan, Stitch
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="these read real frames")

REPO = Path(__file__).resolve().parents[1]


def _render(plan: Plan, out: Path, durations: dict[str, float]) -> Path:
    from vid.compile import compile_plan

    subprocess.run(compile_plan(plan, str(out), durations=durations), check=True, capture_output=True)
    return out


@pytest.fixture(scope="module")
def clips():
    return ensure_clips()


@pytest.fixture(scope="module")
def hard_cut(clips, tmp_path_factory):
    """alpha then bravo, joined with nothing in between."""
    out = tmp_path_factory.mktemp("v") / "hard.mp4"
    plan = Plan(source=str(clips["alpha"].path)).with_operation(Stitch(sources=[str(clips["bravo"].path)]))
    return _render(plan, out, {})


@pytest.fixture(scope="module")
def dissolve(clips, tmp_path_factory):
    """The same two clips, genuinely blended over 0.8s."""
    out = tmp_path_factory.mktemp("v") / "xfade.mp4"
    a, b = str(clips["alpha"].path), str(clips["bravo"].path)
    plan = Plan(source=a).with_operation(Stitch(sources=[b], transition="dissolve", transition_duration=0.8))
    return _render(plan, out, {a: 3.0, b: 3.0})


def test_the_blend_check_tells_a_dissolve_from_a_hard_cut(hard_cut, dissolve):
    """Both directions. This is the whole point of the check.

    On the cut, the sampled middle frame lands on TOP of one side -- measured at
    distance 267 from before and 1 from after, which is to say it IS the after
    frame. On the dissolve it sits between them, roughly 133 from each. A check
    that only ever saw the dissolve would pass while measuring nothing.
    """
    blended = checks.check_transition_at(str(dissolve), 2.6)
    assert blended.held, f"a real dissolve was not detected: {blended.line()}"

    cut = checks.check_transition_at(str(hard_cut), 3.0)
    assert not cut.held, (
        f"a HARD CUT passed the blend check: {cut.line()}\nThe check is not measuring what it claims to measure."
    )
    assert "hard cut" in cut.note


def test_a_transition_between_lookalike_clips_refuses_rather_than_guessing(clips, tmp_path):
    """When no measurement could decide, say so.

    Joining a clip to ITSELF gives two sides that are identical, so nothing about
    the middle frame can reveal whether they were blended. The honest answer is
    not-decidable, and reporting a confident pass here would be the exact failure
    this whole tool is built to avoid.
    """
    a = str(clips["alpha"].path)
    plan = Plan(source=a).with_operation(Stitch(sources=[a], transition="dissolve", transition_duration=0.8))
    out = _render(plan, tmp_path / "same.mp4", {a: 3.0})

    result = checks.check_transition_at(str(out), 2.6)
    assert not result.held
    assert "too alike" in result.note


def test_duration_reports_the_measurement_beside_the_expectation(dissolve):
    good = checks.check_duration(str(dissolve), 5.2)
    assert good.held
    assert "5.2" in good.measured

    bad = checks.check_duration(str(dissolve), 9.0)
    assert not bad.held
    assert "9.00" in bad.expected, "a violation must show what was WANTED"
    assert "5.2" in bad.measured, "a violation must show what was MEASURED, or nobody can tell how wrong it is"


def test_resolution_and_audio(dissolve):
    assert checks.check_resolution(str(dissolve), "640x360").held
    assert not checks.check_resolution(str(dissolve), "1920x1080").held
    assert checks.check_audio(str(dissolve)).held, "the fixtures carry a sine tone"


def test_a_silent_track_fails_even_though_a_track_exists(clips, tmp_path):
    """Present is not the same as audible, and only one of those is useful."""
    out = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(clips["alpha"].path),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-shortest",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    result = checks.check_audio(str(out))
    assert not result.held, "a track of digital silence passed as audio"


def test_verify_refuses_when_given_nothing_to_check():
    from vid.schemas import VidError

    with pytest.raises(VidError, match="nothing to check"):
        checks.report([])


def test_the_report_names_the_absence_of_a_model(dissolve):
    """The claim that makes these results trustworthy should be on the output."""
    text, passed = checks.report([checks.check_duration(str(dissolve), 9.0)])
    assert not passed
    assert "not opinions" in text


def test_an_unreadable_path_names_the_remedy_not_just_the_raw_stderr(tmp_path):
    """A real ffprobe failure -- no such file -- must come back with something
    a caller can act on, not just ffprobe's own stderr with nothing added."""
    missing = tmp_path / "does-not-exist.mp4"

    with pytest.raises(VidError) as failure:
        checks.duration(str(missing))

    message = str(failure.value)
    assert "ffprobe could not read" in message
    assert "vid check" in message, "the failure must name a remedy, not only report the raw stderr"


@pytest.mark.parametrize(
    "source",
    ["color=c=0x181818:s=160x90:d=1", "color=c=black:s=160x100:d=1,drawbox=x=0:y=0:w=16:h=100:color=white:t=fill"],
)
def test_black_thresholds_change_real_measurements_and_are_reported(source, tmp_path):
    from vid import lib

    path = str(tmp_path / "dark.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", source, path], check=True, capture_output=True)
    if "drawbox" in source:
        assert checks.check_no_black_frames(path).held
        options = {"frame_threshold": 0.85}
        assert not checks.check_no_black_frames(path, **options).held
    else:
        assert not checks.check_no_black_frames(path).held
        options = {"pixel_threshold": 0.02}
        assert checks.check_no_black_frames(path, **options).held
    _, report = lib.verify(
        path,
        expect_no_black_frames=True,
        pixel_threshold=options.get("pixel_threshold", 0.1),
        frame_threshold=options.get("frame_threshold", 0.98),
    )
    assert "pix_th=" in report
    assert "pic_th=" in report
    assert "longest_black=0.5s" in report


def test_black_analysis_failure_never_passes_even_with_partial_measurements(monkeypatch):
    for code, stdout in [(1, "frame=20\n"), (0, "frame=0\n"), (0, "")]:
        monkeypatch.setattr(
            checks.subprocess,
            "run",
            lambda *args, code=code, stdout=stdout, **kwargs: subprocess.CompletedProcess(
                args[0], code, stdout=stdout, stderr="black_duration:0.0\nbad decode"
            ),
        )
        with pytest.raises(VidError, match="analysis failed"):
            checks.check_no_black_frames("damaged.mp4")


@pytest.mark.parametrize("name", ["pixel_threshold", "frame_threshold", "longest"])
@pytest.mark.parametrize("value", [-0.01, float("inf"), float("nan")])
def test_black_thresholds_reject_invalid_numbers(name, value):
    with pytest.raises(VidError, match="finite"):
        checks.check_no_black_frames("unused.mp4", **{name: value})


@pytest.mark.parametrize("name", ["pixel_threshold", "frame_threshold"])
def test_black_thresholds_reject_fractions_above_one(name):
    with pytest.raises(VidError, match="fraction"):
        checks.check_no_black_frames("unused.mp4", **{name: 1.01})


def test_missing_or_nonvideo_input_is_not_a_black_detection_pass(tmp_path):
    for path in [tmp_path / "missing.mp4", REPO / "tests" / "fixtures" / "undecodable.mp4"]:
        with pytest.raises(VidError, match="analysis failed"):
            checks.check_no_black_frames(str(path))
    audio = str(tmp_path / "audio.wav")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=0.2", audio],
        check=True,
        capture_output=True,
    )
    with pytest.raises(VidError, match="analysis failed"):
        checks.check_no_black_frames(audio)


def test_verify_cli_forwards_thresholds(monkeypatch):
    from typer.testing import CliRunner

    from vid import lib
    from vid.cli import app

    seen = {}

    def verify(video, **kwargs):
        seen.update(kwargs)
        return False, "threshold measurement"

    monkeypatch.setattr(lib, "verify", verify)
    result = CliRunner().invoke(
        app,
        [
            "verify",
            "a.mp4",
            "--expect-no-black-frames",
            "--pixel-threshold",
            "0.02",
            "--frame-threshold",
            "0.9",
            "--longest-black",
            "0.3",
        ],
    )
    assert result.exit_code == 1
    assert seen["pixel_threshold"] == 0.02
    assert seen["frame_threshold"] == 0.9
    assert seen["longest_black"] == 0.3


@pytest.mark.parametrize("prefix", [0, 0.2])
@pytest.mark.parametrize("black_length", [0.04, 0.12])
def test_terminal_black_run_includes_last_frame_duration(tmp_path, prefix, black_length):
    path = str(tmp_path / "terminal.mp4")
    source = f"color=c=black:s=96x64:r=25:d={prefix + black_length}"
    if prefix:
        source += f",drawbox=color=white:t=fill:enable='lt(t,{prefix})'"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", source, path],
        check=True,
        capture_output=True,
    )
    result = checks.check_no_black_frames(path, longest=0)
    assert not result.held
    assert result.measured == f"longest {black_length:.2f}s"
    assert not checks.check_no_black_frames(path, longest=black_length - 0.01).held
    assert checks.check_no_black_frames(path, longest=black_length + 0.001).held


def test_closed_black_run_does_not_include_following_nonblack_frame(tmp_path):
    path = str(tmp_path / "closed.mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=96x64:r=25:d=0.12,drawbox=color=white:t=fill:enable='gte(t,0.04)'",
            path,
        ],
        check=True,
        capture_output=True,
    )
    result = checks.check_no_black_frames(path, longest=0.04)
    assert result.held
    assert result.measured == "longest 0.04s"


def test_terminal_black_measurement_uses_same_timestamp_origin(tmp_path):
    path = str(tmp_path / "shifted.mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=96x64:r=25:d=0.04",
            "-output_ts_offset",
            "2",
            path,
        ],
        check=True,
        capture_output=True,
    )
    result = checks.check_no_black_frames(path, longest=0.05)
    assert result.held
    assert result.measured == "longest 0.04s"
