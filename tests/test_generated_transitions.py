"""Tier 3: a written transition, and the gate that decides whether to trust it.

The gate is the feature. Generation is the easy half -- an experiment over six
descriptions produced six expressions that all parsed, and one of them moved
wrongly anyway. So these tests care much more about what gets REFUSED than about
what gets through.
"""

from __future__ import annotations

import pytest

from tests.fixtures import ensure_clips, have_ffmpeg
from vid.compile import compile_plan
from vid.plan import Plan, Stitch
from vid.schemas import VidError
from vid.transitions import probe, resolve_with_clips

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="the gate renders real frames")


@pytest.fixture(scope="module")
def clips():
    built = ensure_clips()
    return str(built["alpha"].path), str(built["bravo"].path)


def test_a_real_blend_passes_the_gate(clips):
    first, second = clips
    passed, detail = probe("A*(1-P)+B*P", first, second, 0.8)
    assert passed, detail


def test_a_geometric_wipe_passes_too(clips):
    """Wipes CHOOSE between inputs rather than mixing them, and must still count."""
    first, second = clips
    passed, _ = probe("if(gt(X,W*(1-P)),B,A)", first, second, 0.8)
    assert passed


def test_the_exact_expression_a_model_got_wrong_is_caught(clips):
    """The one failure from the experiment, now caught automatically.

    `if(lt(P,0.5), A*(1-2*P), B*(2*P-1))` reads as an obviously correct fade
    through black -- scale one input down, bring the other up, zero is black. It
    is not: xfade evaluates per YUV plane, so driving every plane to zero gives
    black luma AND extreme chroma. It compiles. It renders. It moves wrongly.

    This is the case that justifies the whole gate, so it is the case that must
    never silently pass.
    """
    first, second = clips
    passed, detail = probe("if(lt(P,0.5), A*(1-2*P), B*(2*P-1))", first, second, 0.8)
    assert not passed, "the YUV trap passed the gate -- the gate is not working"
    assert "does not blend" in detail


def test_an_expression_that_does_nothing_is_caught(clips):
    """`A` compiles and is a perfectly valid expression. It is also a hard cut."""
    first, second = clips
    passed, detail = probe("A", first, second, 0.8)
    assert not passed
    assert "does not blend" in detail


def test_an_expression_that_will_not_parse_is_caught(clips):
    first, second = clips
    passed, detail = probe("this is not an expression", first, second, 0.8)
    assert not passed
    assert "does not compile" in detail


def test_a_plan_will_not_render_an_unverified_expression():
    """The invariant, enforced at compile rather than trusted.

    A plan can be hand-edited, and a plan file is exactly where someone would
    paste an expression they found somewhere. `transition_verified` is not a
    label the compiler takes on faith.
    """
    durations = {"a.mp4": 3.0, "b.mp4": 3.0}
    unverified = Plan(source="a.mp4").with_operation(
        Stitch(sources=["b.mp4"], transition="custom", transition_duration=0.8, transition_expr="A*(1-P)+B*P")
    )
    with pytest.raises(VidError, match="UNVERIFIED"):
        compile_plan(unverified, "out.mp4", durations=durations)


def test_a_custom_transition_with_no_expression_is_refused():
    durations = {"a.mp4": 3.0, "b.mp4": 3.0}
    empty = Plan(source="a.mp4").with_operation(Stitch(sources=["b.mp4"], transition="custom", transition_duration=0.8))
    with pytest.raises(VidError, match="needs an expression"):
        compile_plan(empty, "out.mp4", durations=durations)


def test_a_verified_expression_reaches_ffmpeg_intact():
    durations = {"a.mp4": 3.0, "b.mp4": 3.0}
    good = Plan(source="a.mp4").with_operation(
        Stitch(
            sources=["b.mp4"],
            transition="custom",
            transition_duration=0.8,
            transition_expr="A*(1-P)+B*P",
            transition_verified=True,
        )
    )
    command = compile_plan(good, "out.mp4", durations=durations)
    graph = next(part for part in command if "xfade" in part)
    assert "transition=custom" in graph
    assert "expr='A*(1-P)+B*P'" in graph


def test_tier_1_never_reaches_a_model(clips):
    """A preset name resolves with no provider and no probe."""
    first, second = clips
    preset, requested, rationale, expression = resolve_with_clips("dissolve", first, second, 0.8, intelligence=None)
    assert (preset, requested, rationale, expression) == ("dissolve", None, None, None)


def test_a_description_without_a_provider_still_refuses(clips):
    first, second = clips
    with pytest.raises(VidError, match="not configured"):
        resolve_with_clips("something soft and dreamlike", first, second, 0.8, intelligence=None)


def test_generation_needs_the_clips_to_prove_against(clips):
    """Refusing without clips is the honest answer, not a degraded one.

    Tier 3 cannot be verified without the pair it will join, and an unverifiable
    generated expression is exactly what this feature exists to not ship.
    """

    class SaysNone:
        def run(self, request):
            return type("R", (), {"text": "NONE\nnothing here matches", "error": None})()

    with pytest.raises(VidError, match="requires the clips"):
        resolve_with_clips("a bespoke swirling dissolve", None, None, 0.8, SaysNone())
