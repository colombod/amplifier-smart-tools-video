"""The look verbs, measured rather than admired.

ON THE FIXTURE, because the first version of these tests was wrong in a way that
would have passed. A flat neutral grey measures EVERY look as identical --
`colorbalance` weights shadows, midtones and highlights separately, and a flat
frame has only one tone for them to act on. Warm and cool produced byte-identical
files, and the bug was in the fixture, not the filter.

So these use `testsrc2`, which has real tonal and colour range. A grade can only
be measured on content that has something to grade.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.fixtures import have_ffmpeg
from vid.compile import compile_plan
from vid.looks import LOOKS, resolve_look, vignette_filter
from vid.plan import Grade, Lut, Plan, Trim, Vignette
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="these render real frames")


def _content(path, pattern: str = "testsrc2") -> str:
    """A clip with actual tonal range. See the module docstring.

    The separator is chosen, not assumed: `testsrc2` takes its first option after
    an `=`, while `color=c=...` already has one and needs a `:`. Getting that
    wrong builds `color=c=grey=s=320x180`, which ffmpeg rejects -- and the test
    then fails for a reason that has nothing to do with what it is testing.
    """
    joiner = ":" if "=" in pattern else "="
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"{pattern}{joiner}s=320x180:r=15:d=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return str(path)


def _render(plan: Plan, out) -> str:
    subprocess.run(compile_plan(plan, str(out), has_audio=False), check=True, capture_output=True)
    return str(out)


def _brightness(path: str, crop: str) -> float:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vf", f"crop={crop}",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True,
    ).stdout
    return sum(raw) / len(raw) if raw else 0.0


def test_a_vignette_really_does_darken_the_corners(tmp_path):
    """The acceptance clause, measured by sampling regions rather than by looking.

    A flat grey IS the right fixture here -- unlike a colour grade, a vignette
    acts on position, not tone, so a uniform frame isolates exactly the effect.
    """
    flat = _content(tmp_path / "flat.mp4", "color=c=0x808080")
    out = _render(Plan(source=flat).with_operation(Vignette()), tmp_path / "vig.mp4")

    for path, label in ((flat, "source"), (out, "vignetted")):
        centre, corner = _brightness(path, "60:60:130:60"), _brightness(path, "60:60:0:0")
        if label == "source":
            assert abs(centre - corner) < 2.0, "the fixture was not uniform to begin with"
        else:
            assert corner < centre * 0.8, (
                f"corner {corner:.1f} is not meaningfully darker than centre {centre:.1f}"
            )


def test_strength_zero_is_nearly_nothing_and_one_is_a_lot(tmp_path):
    flat = _content(tmp_path / "flat.mp4", "color=c=0x808080")
    darkness = {}
    for strength in (0.0, 1.0):
        out = _render(
            Plan(source=flat).with_operation(Vignette(strength=strength)),
            tmp_path / f"v{strength}.mp4",
        )
        darkness[strength] = _brightness(out, "60:60:130:60") - _brightness(out, "60:60:0:0")
    assert darkness[1.0] > darkness[0.0] * 2, "strength is not doing much"


def test_warm_and_cool_move_in_opposite_directions_and_far_enough(tmp_path):
    """Direction AND magnitude, because the first version had direction only.

    The original weights produced a Lab b* shift of +0.23 -- correct in sign and
    invisible in practice. A test asserting only "it moved the right way" passes
    happily on a grade nobody can see, so this asserts a floor as well.
    """
    from vid.color import measure_video

    source = _content(tmp_path / "src.mp4")
    base = measure_video(source)

    shifts = {}
    for look in ("warm", "cool"):
        out = _render(
            Plan(source=source).with_operation(Grade(look=look)), tmp_path / f"{look}.mp4"
        )
        shifts[look] = measure_video(out).mean[2] - base.mean[2]

    assert shifts["warm"] > 1.0, f"warm shifted b* by only {shifts['warm']:+.2f} -- invisible"
    assert shifts["cool"] < -1.0, f"cool shifted b* by only {shifts['cool']:+.2f} -- invisible"


def test_noir_removes_the_colour(tmp_path):
    from vid.color import measure_video

    source = _content(tmp_path / "src.mp4")
    assert abs(measure_video(source).mean[1]) > 2.0, "the fixture has no colour to remove"

    out = _render(Plan(source=source).with_operation(Grade(look="noir")), tmp_path / "noir.mp4")
    result = measure_video(out)
    assert abs(result.mean[1]) < 2.0 and abs(result.mean[2]) < 2.0, "noir left colour behind"


def test_every_named_look_renders(tmp_path):
    """A look in the catalogue that does not compile is a broken promise."""
    source = _content(tmp_path / "src.mp4")
    for look in LOOKS:
        _render(Plan(source=source).with_operation(Grade(look=look)), tmp_path / f"{look}.mp4")


def test_an_unknown_look_is_refused_with_the_list(tmp_path):
    with pytest.raises(VidError) as failure:
        resolve_look("cinematic")
    message = str(failure.value)
    assert "warm" in message and "noir" in message, "a refusal must name the alternatives"
    assert "recolor" in message, "and point at the escape hatch for something specific"


def test_a_missing_lookup_table_is_refused_before_ffmpeg_sees_it(tmp_path):
    source = _content(tmp_path / "src.mp4")
    plan = Plan(source=source).with_operation(Lut(path=str(tmp_path / "nope.cube")))
    with pytest.raises(VidError, match="No such lookup table"):
        compile_plan(plan, str(tmp_path / "o.mp4"), has_audio=False)


def test_a_whole_chain_is_still_one_ffmpeg_invocation(tmp_path):
    """The architectural promise: five operations, one decode, one encode."""
    source = _content(tmp_path / "src.mp4")
    plan = (
        Plan(source=source)
        .with_operation(Trim(start=0.0, end=1.5))
        .with_operation(Grade(look="warm"))
        .with_operation(Vignette())
    )
    command = compile_plan(plan, str(tmp_path / "out.mp4"), has_audio=False)

    assert command.count("-i") == 1, "more than one input means more than one decode"
    graph = next(part for part in command if "vignette" in part)
    assert "trim" in graph and "colorbalance" in graph, "the chain was split across passes"
    subprocess.run(command, check=True, capture_output=True)


def test_the_look_verbs_need_no_provider(tmp_path):
    source = _content(tmp_path / "src.mp4")
    command = compile_plan(
        Plan(source=source).with_operation(Grade(look="punchy")),
        str(tmp_path / "o.mp4"), has_audio=False,
    )
    assert command[0] == "ffmpeg"


def test_vignette_strength_outside_zero_to_one_is_refused():
    with pytest.raises(VidError, match="between 0 and 1"):
        vignette_filter(1.5)
