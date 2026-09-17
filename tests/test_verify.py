"""The verification checks, proved against videos we built on purpose.

THE LOAD-BEARING TEST is `test_the_blend_check_tells_a_dissolve_from_a_hard_cut`.
A check that passes on a real crossfade proves nothing on its own -- it has to
FAIL on a hard cut, or it is not measuring what it claims. Both directions, same
check, or the assertion is theatre.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.fixtures import ensure_clips, have_ffmpeg
from vid import verify as checks
from vid.plan import Plan, Stitch
from vid.probe import duration as probe_duration

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
    plan = Plan(source=str(clips["alpha"].path)).with_operation(
        Stitch(sources=[str(clips["bravo"].path)])
    )
    return _render(plan, out, {})


@pytest.fixture(scope="module")
def dissolve(clips, tmp_path_factory):
    """The same two clips, genuinely blended over 0.8s."""
    out = tmp_path_factory.mktemp("v") / "xfade.mp4"
    a, b = str(clips["alpha"].path), str(clips["bravo"].path)
    plan = Plan(source=a).with_operation(
        Stitch(sources=[b], transition="dissolve", transition_duration=0.8)
    )
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
        f"a HARD CUT passed the blend check: {cut.line()}\n"
        "The check is not measuring what it claims to measure."
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
    assert "9.00" in bad.expected and "5.2" in bad.measured, (
        "a violation must show what was measured next to what was wanted, "
        "or the caller cannot tell how wrong it is"
    )


def test_resolution_and_audio(dissolve):
    assert checks.check_resolution(str(dissolve), "640x360").held
    assert not checks.check_resolution(str(dissolve), "1920x1080").held
    assert checks.check_audio(str(dissolve)).held, "the fixtures carry a sine tone"


def test_a_silent_track_fails_even_though_a_track_exists(clips, tmp_path):
    """Present is not the same as audible, and only one of those is useful."""
    out = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(clips["alpha"].path),
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-map", "0:v", "-map", "1:a", "-shortest", "-c:v", "copy", "-c:a", "aac", str(out)],
        check=True, capture_output=True,
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
