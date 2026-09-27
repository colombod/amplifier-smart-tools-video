"""Every plan model with numeric fields is either guarded or knowingly not.

WHY THIS FILE EXISTS. Four review rounds each found one more unvalidated model,
and each surfaced only after the previous was fixed: `Overlay`+`Motion`, then
`Mask`, then `LayerAudio`. Every sweep was one model wide, because the class
was taken to be "models near the reported bug".

It is not. It is a STRUCTURAL property: a numeric field on a plan model that
the library door cannot reach, so `lib.*`'s guards never see it and only a JSON
plan can set it. That property is enumerable in one pass -- which is what this
file does, once, instead of finding one more every round.

The enumeration found TEN such models where hand-searching had found three.

This is a RATCHET, not a demand that everything be fixed today. The models
below are known to be unguarded and are recorded as debt, several with real
defects filed separately. What the test forbids is ADDING an eleventh without
noticing: a new unguarded model fails here, and guarding an existing one fails
here too until it is struck from the list. Either way the list cannot silently
drift out of date.
"""

from collections.abc import Callable
from decimal import Decimal
import inspect
import json
from pathlib import Path
import subprocess
from typing import Any, Optional, get_args, get_origin

from pydantic import BaseModel, ValidationError
import pytest

from tests.fixtures import ensure_long_base_clip, ensure_square_clip, have_ffmpeg
from vid.compile import compile_plan
import vid.plan as plan_module
from vid.plan import AudioMix, AudioReplace, Cut, Mask, Motion, Overlay, Plan, Trim, Zoom
from vid.schemas import VidError

#: Numeric fields that accept a value no real edit could mean, PER FIELD, as
#: measured by probing them -- not per model, and not inferred from whether the
#: class happens to own a validator.
#:
#: WHY PER FIELD. The model-level list could not express the true state, which
#: is that a model can own a validator and still leave numbers unchecked.
#: Probing this tree found exactly that: `Key` validates nothing among
#: threshold/similarity/blend, `LayerAudio` leaves gain_db and base_gain_db
#: open, `Overlay` leaves x/y/start/end, `Motion` leaves to_x/to_y -- while the
#: previous check reported all four as GUARDED because each owns some private
#: method. It also called `AudioReplace` debt when every one of its numbers is
#: in fact checked. Wrong in both directions.
#:
#: CORRECTION CARRIED FORWARD. An earlier note claimed these were "reachable
#: only through a JSON plan, so `lib.*` cannot protect it". That was asserted,
#: not checked, and was wrong: `lib.cut` and `lib.zoom` construct the model
#: directly, so the CLI reaches them too. Cut, Retime and Zoom were fixed
#: rather than deferred once that came out.
KNOWN_UNGUARDED: dict[str, set[str]] = {
    "Motion": {"to_x", "to_y"},
    "Overlay": {"end", "x", "y"},
    "Plan": {"plan_format"},
    "Recolor": {"reference_mean", "reference_std", "source_mean", "source_std", "strength"},
    "Stitch": {"transition_duration"},
    "Trim": {"end", "start"},
    "Vignette": {"strength"},
}

#: Fully guarded, by measurement: AudioMix, AudioReplace, Cut, LayerAudio,
#: Mask, Retime, Zoom.
#:
#: THE SWEEP. Every field above was rendered at +/-1e9 through vid's real
#: graph. That found FIVE defects the list had been carrying as ordinary debt,
#: each one a value vid accepted and ffmpeg then died on:
#:
#:   AudioMix.level=1e9              rc=234  Conversion failed!
#:   LayerAudio.gain_db=1e9          rc=234  Conversion failed!
#:   Stitch.transition_duration=1e9  rc=222  out of range [0 - 60]
#:   Key.similarity=1e9              rc=222  out of range [1e-05 - 1]
#:   RampPoint.at=1e9                NEVER RETURNED
#:
#: The last is the worst kind: not an error, a hang. All five are now guarded
#: at bounds ffmpeg declares, except `RampPoint.at`, which has no declared
#: range and is labelled in plan.py as our judgment with its measurements.
#:
#: WHAT REMAINS is open at BOTH ends -- these reject neither +1e9 nor -1e9, so
#: declaring them "open above" would not have cleared them, and did not.
#: `Stitch.transition_duration` stays listed because its guard only applies
#: when a transition is actually set, which the probe's minimal instance does
#: not do -- the guard is real, the probe cannot see it.


#: Leaf types that carry a number a caller can put 1e9 into.
#: `Decimal` is here because it is numeric and NOTHING in the old predicate
#: could see it -- it is not `int`, not `float`, and its string form matches no
#: pattern that was being tested for.
NUMERIC_LEAVES: tuple[type, ...] = (int, float, Decimal)


def _numeric_tuple_width(annotation: Any) -> int:
    """How many slots, if this is a fixed-width tuple of numbers. 0 if it is not.

    A DIFFERENT QUESTION FROM `_carries_numbers`, and kept separate for that
    reason. Discovery asks "is there a number in here to probe at all"; this
    asks "how wide a payload do I build". Folding them together is what made
    the old predicate answer both with one string prefix check.

    Structural via `get_args`, so `tuple[float, float, float]` and any
    equivalent spelling give the same answer.
    """
    if get_origin(annotation) is not tuple:
        return 0
    args = get_args(annotation)
    if not args or Ellipsis in args:
        return 0
    return len(args) if all(isinstance(a, type) and a in NUMERIC_LEAVES for a in args) else 0


def _carries_numbers(annotation: Any) -> bool:
    """Does this annotation carry a number anywhere inside it?

    RECURSIVE, VIA `typing.get_args`, AND THAT IS THE WHOLE POINT. The predicate
    this replaces matched SPELLINGS: `int`, `float`, and the literal strings
    `"int | None"` and `"float | None"`, plus a string-prefix check for tuples.
    Four common SPELLINGS of the same numeric types were invisible to it,
    confirmed by injecting one scratch model per spelling and running the
    new-model guard against each:

        typing.Optional[float]   detected=False   ratchet PASSED (invisible)
        list[float]              detected=False   ratchet PASSED (invisible)
        Decimal                  detected=False   ratchet PASSED (invisible)
        int | float              detected=False   ratchet PASSED (invisible)
        float (control)          detected=True    ratchet FAILED (caught)

    `Optional[float]` MEANS `float | None`. A contributor writing the first
    spelling in a codebase that uses the second would have added an unguarded
    numeric field and seen the ratchet stay green -- no verdict at all,
    presented as a clean one.

    Matching structure rather than text fixes the whole class instead of the
    four instances. A fifth spelling nobody has thought of yet decomposes to
    the same leaves.

    NESTED MODELS ARE NOT DESCENDED INTO. `Retime.ramp` is `list[RampPoint]`,
    and RampPoint is swept as a model in its own right, so its numbers are
    already covered. Descending would double-report them and make the debt
    list disagree with itself about who owns a field.
    """
    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return False
        return any(annotation is leaf for leaf in NUMERIC_LEAVES)

    args = get_args(annotation)
    if not args:
        return False
    return any(_carries_numbers(arg) for arg in args if arg is not type(None))


def _numeric_models() -> dict[str, list[str]]:
    """Plan models carrying int/float fields, and which fields those are."""
    found = {}
    for name, obj in vars(plan_module).items():
        if not (inspect.isclass(obj) and issubclass(obj, BaseModel) and obj is not BaseModel):
            continue
        if obj.__module__ != "vid.plan":
            continue
        # STRUCTURE, NOT SPELLING -- see `_carries_numbers`. This used to be a
        # list of literal spellings plus a tuple prefix check, and four equally
        # ordinary ways of writing the same types went unseen. (Not to be
        # confused with the four MODELS an earlier proxy miscertified -- see
        # `test_no_numeric_field_is_invisible_to_the_ratchet`.)
        numeric = [field for field, info in obj.model_fields.items() if _carries_numbers(info.annotation)]
        if numeric:
            found[name] = numeric
    return found


#: Seeds for models whose required fields are not plain numbers or strings.
#: Explicit rather than inferred: a builder clever enough to guess these is a
#: builder that can guess WRONG and silently probe a different model than the
#: one named. `Retime` takes no required field but its validator demands
#: exactly one of speed/ramp.
SEEDS: dict[str, dict] = {
    "Mask": {"kind": "rect"},
    "Recolor": {
        "reference": "ref.png",
        "source_mean": (0.0, 0.0, 0.0),
        "source_std": (1.0, 1.0, 1.0),
        "reference_mean": (0.0, 0.0, 0.0),
        "reference_std": (1.0, 1.0, 1.0),
    },
    "Retime": {"speed": 1.0},
    "Stitch": {"sources": ["a.mp4", "b.mp4"]},
    "Plan": {"source": "in.mp4"},
    "AudioMix": {"track": "a.wav"},
    "AudioReplace": {"track": "a.wav"},
    "Overlay": {"layer": "over.mp4"},
}

#: Values no legitimate field should accept.
ABSURD = (-1e9, 1e9)

#: Fields legitimately bounded on ONE end only, with the render that establishes
#: it. NEITHER `any` nor `all` over both ends is the rule, and this file has now
#: shipped both errors:
#:
#:   `any`  certified a field guarded when only one end was refused -- so a
#:          genuinely two-sided field, guarded below and open above, passed.
#:   `all`  demanded both ends of fields whose upper end is legitimately open,
#:          which would report a CORRECT guard as debt.
#:
#: Measured against a 6.0s source, validation bypassed:
#:   Cut.end=1e9        -> 1.0s   "remove everything from 1s on", clamped right
#:   Zoom.at=1e9        -> 6.0s   centre beyond the file, clamped
#:   Zoom.duration=1e9  -> 6.0s   clamped
#:
#: Anything NOT listed defaults to "both", so a new field is reported as debt
#: until someone renders it and writes the evidence down here. Conservative in
#: the direction that costs a false alarm rather than a false certificate.
#: SIX OF THE TWENTY-ONE ENTRIES HERE WERE FALSE, and the comment above them
#: said "each was rendered". An outside reviewer rendered all twenty-one at
#: +1e9 and found:
#:
#:   Overlay.start      HUNG FOREVER -- tpad=start_duration=1000000000.000000
#:   Retime.speed       rc=234, and rc=0 with a one-frame stub on silent video
#:   RampPoint.speed    rc=234, the same collapse reached through a ramp
#:   Key.threshold      rc=222 "Numerical result out of range"
#:   Stitch.transition_offset  rc=0, because the field was READ NOWHERE
#:   Trim.start         a 261-byte file with NO STREAMS, rc=0
#:
#: THE ROOT CAUSE WAS THE SHAPE OF THE PROBE, not the care of whoever wrote
#: the entries. `_unguarded_fields` asks pydantic one question -- "does
#: construction raise?" -- and that question cannot see a render. So an
#: exemption FROM that probe could only ever be justified in prose, and prose
#: is not re-run. Three of the quoted measurements reproduce exactly; the six
#: above do not; and nothing in the file distinguished them.
#:
#: SO THE REMEDY IS NOT A BETTER COMMENT. Every entry that survives is now
#: RENDERED BY THIS SUITE at the end it claims is open -- see
#: `test_every_one_sided_exemption_is_re_rendered` -- and every entry that
#: gained a real bound was DELETED rather than reworded, because a field with a
#: genuine upper bound needs no exemption at all. Twenty-one entries became
#: thirteen that way.
ONE_SIDED: dict[tuple[str, str], str] = {
    ("Cut", "end"): "below",
    ("Zoom", "at"): "below",
    ("Zoom", "duration"): "below",
    ("Zoom", "to"): "below",
    ("Mask", "radius"): "below",
    # NOT `Mask.feather`. It looked identical to these and is not: at 1e9
    # ffmpeg failed the render outright, rc=222 "Numerical result out of
    # range". It is now bounded at 1024 in plan.py and stays two-sided here.
    ("AudioMix", "start"): "below",
    ("AudioReplace", "start"): "below",
    ("Trim", "start"): "below",
    ("Trim", "end"): "below",
    ("Overlay", "x"): "below",
    ("Overlay", "y"): "below",
    ("Overlay", "end"): "below",
    ("Motion", "to_x"): "below",
    ("Motion", "to_y"): "below",
    ("Motion", "start"): "below",
    ("Motion", "duration"): "below",
    # GONE, EACH FOR A STATED REASON -- none of them reworded:
    #   Overlay.start     bounded to 3600s; 1e8 and 1e9 wedge the render
    #   Retime.speed      bounded to 0.01..100x at both ends
    #   RampPoint.speed   same bounds, same setpts
    #   Key.threshold     bounded to ffmpeg's declared 0..1
    #   Stitch.transition_offset   the FIELD is gone; it was never read
    #
    # `Trim.start` stays, and is the one entry whose exemption is honest for an
    # unusual reason: the model genuinely cannot judge it, because "past the end
    # of the file" is not a property of the number. It is refused in
    # `compile.trim`, where the duration is known, and the render check below
    # accepts a named refusal as a legitimate outcome.
}


def _ends_for(name: str, field: str) -> tuple[float, ...]:
    """The values this field must refuse, per its declared shape."""
    return {"both": ABSURD, "below": (-1e9,), "above": (1e9,)}[ONE_SIDED.get((name, field), "both")]


def _valid_kwargs(model: type[BaseModel], name: str) -> dict:
    """The smallest kwargs that build this model, seeds first."""
    kwargs = dict(SEEDS.get(name, {}))
    for field, info in model.model_fields.items():
        if field in kwargs or not info.is_required():
            continue
        annotation = str(info.annotation)
        if "float" in annotation:
            kwargs[field] = 1.0
        elif "int" in annotation:
            kwargs[field] = 1
        elif "str" in annotation:
            kwargs[field] = "x"
    return kwargs


def _unguarded_fields(name: str) -> list[str]:
    """Which numeric fields accept a value no real edit could mean.

    PROBES THE PROPERTY, rather than asking whether the class happens to own a
    private callable. That earlier proxy was wrong in BOTH directions, measured
    on this very tree: it certified `Key`, `LayerAudio`, `Overlay` and `Motion`
    as guarded while each carried numeric fields that accepted 1e9, and it
    listed `AudioReplace` as debt when every one of its numbers was checked.
    A test that looks for the APPEARANCE of a safeguard hands out certificates.

    Raises rather than returning [] when a model cannot be built -- an
    unprobeable model silently reported as "nothing unguarded here" is the same
    defect one level up, and four models were being skipped that way.
    """
    model = getattr(plan_module, name)
    numeric = _numeric_models()[name]
    base = _valid_kwargs(model, name)
    try:
        model(**base)
    except Exception as exc:
        raise AssertionError(
            f"cannot build a valid {name} to probe it ({exc}). Add a seed to SEEDS. "
            "A model that cannot be probed must NOT be reported as guarded."
        ) from exc

    unguarded = []
    for field in numeric:
        # EVERY end this field is declared to be bounded on must be refused.
        # `all` over `_ends_for(...)`, not over ABSURD: the set of ends is the
        # per-field rule, and it is now executed rather than asserted in prose.
        if not all(_rejects(model, base, field, value) for value in _ends_for(name, field)):
            unguarded.append(field)
    return unguarded


def _rejects(model: type[BaseModel], base: dict, field: str, value: float) -> bool:
    payload = value
    annotation = model.model_fields[field].annotation
    width = _numeric_tuple_width(annotation)
    if width:
        # One absurd element among valid ones: a guard that checks only the
        # first slot must not read as guarding the whole tuple.
        payload = tuple([1.0] * (width - 1) + [value])
    try:
        model(**{**base, field: payload})
    except ValidationError:
        return True
    except Exception:
        return False
    return False


def test_no_new_model_arrives_unguarded():
    """A new plan model with numeric fields must guard them, or be recorded.

    The failure message names the model, because the whole point is that this
    should be noticed at the moment it is introduced rather than by a reviewer
    four rounds later.
    """
    models = _numeric_models()
    arrived = {}
    for name in models:
        new_fields = set(_unguarded_fields(name)) - KNOWN_UNGUARDED.get(name, set())
        if new_fields:
            arrived[name] = sorted(new_fields)
    assert not arrived, (
        f"numeric field(s) that accept ±1e9 and are not recorded as debt: "
        f"{', '.join(f'{name}.{f}' for name in sorted(arrived) for f in arrived[name])}. "
        "Add a `@model_validator` mirroring the rules `lib.*` enforces, or add the model to "
        "KNOWN_UNGUARDED with a note. A numeric field only a JSON plan can set is exactly how "
        "`Mask.radius` and `LayerAudio.duck_ratio` shipped."
    )


def test_the_debt_list_does_not_go_stale():
    """A model that gains a validator must leave the list.

    Without this the list would quietly become a lie -- naming models as
    unguarded long after someone fixed them, and eroding trust in the entry
    that still matters.
    """
    models = _numeric_models()
    now_guarded = {}
    for name, recorded in KNOWN_UNGUARDED.items():
        if name not in models:
            continue
        fixed = recorded - set(_unguarded_fields(name))
        if fixed:
            now_guarded[name] = sorted(fixed)

    assert not now_guarded, (
        "these are now validated but still recorded as debt: "
        + ", ".join(f"{name}.{f}" for name in sorted(now_guarded) for f in now_guarded[name])
        + ". Strike them from KNOWN_UNGUARDED. (Per FIELD -- the old model-level check would have "
        "told you to remove a whole model because one of its numbers got a guard.)"
    )

    vanished = set(KNOWN_UNGUARDED) - set(models)
    assert not vanished, f"KNOWN_UNGUARDED names models that no longer exist: {', '.join(sorted(vanished))}"


#: Fields this branch actually put a guard on, verified by probing rather than
#: by listing the models whose validators it touched. The earlier version of
#: this test named eight models and asserted each "is guarded" -- and passed,
#: while four of them carried numbers that accepted 1e9.
GUARDED_BY_THIS_BRANCH = sorted(
    [
        ("Cut", "start"),
        ("Cut", "end"),
        ("Zoom", "to"),
        ("Zoom", "duration"),
        ("Zoom", "at"),
        ("Mask", "radius"),
        ("Mask", "feather"),
        ("LayerAudio", "duck_ratio"),
        ("LayerAudio", "duck_threshold"),
        ("LayerAudio", "duck_attack"),
        ("LayerAudio", "duck_release"),
        # Added when the six false exemptions were rendered. Each of these was
        # sitting in ONE_SIDED claiming its upper end was open.
        ("Overlay", "start"),
        ("Retime", "speed"),
        ("RampPoint", "speed"),
        ("Key", "threshold"),
    ]
)


@pytest.mark.parametrize(("name", "field"), GUARDED_BY_THIS_BRANCH, ids=[f"{n}.{f}" for n, f in GUARDED_BY_THIS_BRANCH])
def test_the_fields_this_branch_guarded_stay_guarded(name, field):
    """Pinned per FIELD, so a refactor cannot drop one back to unchecked while
    the model keeps a validator for something else and still looks guarded."""
    assert field not in _unguarded_fields(name), (
        f"{name}.{field} now accepts ±1e9; a JSON plan can set it unchecked again"
    )


#: Geometry that is legal ONLY AS A PAIR, and the reason it needs its own test.
#:
#: THIS IS WHERE THE ONE LIVE FALSE CERTIFICATE LIVED. `_unguarded_fields`
#: varies one field at a time, so setting `Overlay.width=1e9` alone trips the
#: "needs both a width and a height" rule and raises -- and the probe reads any
#: ValidationError as "guarded". So these four never appeared in
#: KNOWN_UNGUARDED, and absence from that list is a positive claim of safety.
#: They were in fact unbounded: set as a legal pair, 1e9 reached ffmpeg and
#: killed the render (rc=234), and the animated path wrote a PARTIAL file
#: before dying (rc=244).
#:
#: A single-field probe cannot ask this question. It needs the pair.
PAIRED_GEOMETRY = [
    ("Overlay", ("width", "height")),
    ("Motion", ("to_width", "to_height")),
]


@pytest.mark.parametrize(("name", "pair"), PAIRED_GEOMETRY, ids=[n for n, _ in PAIRED_GEOMETRY])
def test_paired_geometry_is_bounded_when_set_legally(name, pair):
    """Both fields together, which is the only way a caller can set them."""
    model = getattr(plan_module, name)
    base = _valid_kwargs(model, name)
    for value in (10**9, -(10**9)):
        with pytest.raises(ValidationError):
            model(**{**base, pair[0]: value, pair[1]: value})

    # And the bound must not have swallowed ordinary sizes with the absurd ones.
    model(**{**base, pair[0]: 320, pair[1]: 180})


#: The values that were MEASURED to break a render, each of which must be
#: refused -- and beside them, the values measured to work, which must not be.
#:
#: WHY THIS EXISTS, AND IT IS NOT REDUNDANT WITH THE PROBE ABOVE. Mutation
#: testing this file found one guard whose test could not see it: replacing the
#: speed floor `MIN_SPEED <= speed` with `0.0 < speed` broke the guard and
#: EVERY TEST STILL PASSED. The probe only ever tries +/-1e9, and both versions
#: refuse -1e9 -- so the probe proves a guard EXISTS and says nothing about
#: WHERE IT SITS. Every bound between 0 and 1e9 was unpinned: the 0.01 speed
#: floor could have been widened to 0.000001 silently, and `speed=0.001` was
#: measured to never return.
#:
#: So each entry here is a specific measurement, and the pair of lists pins the
#: bound from both sides -- a guard that is too tight fails as loudly as one
#: that is too loose.
MEASURED_HAZARDS = [
    (
        "Retime.speed=0.001 -- render never returned, output still growing at 40s",
        lambda: plan_module.Retime(speed=0.001),
    ),
    ("Retime.speed=1e6 -- rc=234 Conversion failed! (3e5 still renders)", lambda: plan_module.Retime(speed=1e6)),
    ("Retime.speed=2e6 -- 1/speed formats to the literal '0.000000'", lambda: plan_module.Retime(speed=2e6)),
    ("RampPoint.speed=0.001 -- the same collapse through a ramp", lambda: plan_module.RampPoint(at=1.0, speed=0.001)),
    ("RampPoint.speed=1e7 -- rc=234 Conversion failed!", lambda: plan_module.RampPoint(at=1.0, speed=1e7)),
    ("Overlay.start=1e8 -- render never returned", lambda: Overlay(source=LAYER, start=1e8)),
    ("Overlay 65534px pair -- rc=234 Conversion failed!", lambda: Overlay(source=LAYER, width=65534, height=65534)),
    ("Motion 65536px pair -- rc=244, and a PARTIAL file left behind", lambda: Motion(to_width=65536, to_height=65536)),
    ("Key.threshold=1.1 -- outside ffmpeg's declared 0..1", lambda: plan_module.Key(kind="lumakey", threshold=1.1)),
]

MEASURED_GOOD = [
    ("Retime.speed=100 -- rc=0", lambda: plan_module.Retime(speed=100.0)),
    ("Retime.speed=0.01 -- rc=0, 600s out in 14.6s of wall clock", lambda: plan_module.Retime(speed=0.01)),
    ("RampPoint.speed=100 -- rc=0", lambda: plan_module.RampPoint(at=1.0, speed=100.0)),
    ("Overlay.start=3600 -- rc=0", lambda: Overlay(source=LAYER, start=3600.0)),
    ("Overlay 16384px pair -- rc=0", lambda: Overlay(source=LAYER, width=16384, height=16384)),
    ("Motion 4096px pair -- rc=0", lambda: Motion(to_width=4096, to_height=4096)),
    ("Key.threshold=1.0 -- ffmpeg's own ceiling", lambda: plan_module.Key(kind="lumakey", threshold=1.0)),
    ("Key.threshold=0.0 -- ffmpeg's own floor", lambda: plan_module.Key(kind="lumakey", threshold=0.0)),
]


@pytest.mark.parametrize(("what", "build"), MEASURED_HAZARDS, ids=[w.split(" --")[0] for w, _ in MEASURED_HAZARDS])
def test_a_value_measured_to_break_a_render_is_refused(what, build):
    """Pins the bound WHERE IT SITS, which the ±1e9 probe cannot do."""
    with pytest.raises(ValidationError):
        build()


@pytest.mark.parametrize(("what", "build"), MEASURED_GOOD, ids=[w.split(" --")[0] for w, _ in MEASURED_GOOD])
def test_a_value_measured_to_render_is_still_accepted(what, build):
    """The other side of the same bound: a guard must not refuse working plans."""
    build()


#: The ceilings that are OURS rather than ffmpeg's, pinned BY VALUE.
#:
#: WHY ASSERT A CONSTANT, which on the face of it tests nothing. Mutation
#: testing the lists above found two widenings the whole suite accepted:
#: `MAX_SPEED` 100 -> 1e6, and `MAX_OFFSET_SECONDS` 3600 -> 1e7. That is not a
#: hole in those lists; it is unavoidable. Both numbers are JUDGEMENTS placed
#: deliberately BELOW the measured failure, so by construction there is no
#: measurement sitting at the bound to notice it moving -- and everything
#: between the bound and the real edge genuinely does render.
#:
#: A measurement cannot pin a judgement. A name can. Moving one of these now
#: has to move this list too, which turns a silent widening into a deliberate
#: edit with the measurements either side of it written down.
#:
#: `MAX_DIMENSION` and `MIN_SPEED` are here for the same reason even though
#: their mutations were caught -- the nearest hazard happens to be close, and
#: that is luck rather than design.
JUDGEMENT_BOUNDS = {
    "MAX_OFFSET_SECONDS": (3600.0, "renders at 3600 and at 1e7; never returns at 1e8"),
    "MIN_SPEED": (0.01, "renders 600s of output at 0.01; never returns at 0.001"),
    "MAX_SPEED": (100.0, "renders at 100; a one-frame stub by 1e3; rc=234 at 1e6"),
    "MAX_DIMENSION": (16384, "renders at 16384 and 32768; rc=234 at 65534, and that edge moves with memory"),
}


@pytest.mark.parametrize(("name", "expected"), sorted((k, v[0]) for k, v in JUDGEMENT_BOUNDS.items()))
def test_a_judgement_bound_cannot_move_quietly(name, expected):
    assert getattr(plan_module, name) == expected, (
        f"{name} changed. It is a judgement, not a measured edge, so nothing else in this "
        f"suite can see it move -- widening it is exactly how a guard stops guarding. "
        f"What was measured either side: {JUDGEMENT_BOUNDS[name][1]}. "
        "If the change is deliberate, re-measure, update this entry, and say so in the commit."
    )


# ---------------------------------------------------------------------------
# Making an exemption earn itself.
#
# EVERYTHING ABOVE THIS LINE ASKS PYDANTIC A QUESTION. That is the whole reason
# six entries in ONE_SIDED were false: "does construction raise?" cannot see a
# render, so an exemption from it could only be argued in a comment, and a
# comment is not re-run. The reviewer who found this also DEMONSTRATED it could
# be laundered -- a fabricated entry plus a real regression left the suite
# green.
#
# So the exemption is now executed. For each surviving entry, the end it claims
# is open is RENDERED through vid's real graph, and the outcome must be one the
# tool could defend:
#
#   a real render   rc=0, inside the timeout, decodable, at least one stream
#   a named refusal a VidError -- for a guard that lives in the compiler, where
#                   a fact the model cannot know (the source's duration) is
#                   available
#
# and anything else fails: a raw ffmpeg error, a hang, or a file with no
# streams in it that ffmpeg nonetheless called a success.
#
# WHAT THIS STILL DOES NOT PROVE, stated plainly rather than left to be
# discovered by the next reviewer:
#
#  1. It renders against ONE 6.0s 640x360 fixture with audio. A field open
#     above here could fail on other material -- a different resolution, a
#     source with no audio stream (which is exactly where `Retime.speed`'s
#     silent collapse hid), or a longer clip.
#  2. It judges MECHANICS, not meaning. A render that succeeds and produces a
#     visibly wrong picture passes this check. `Overlay.x=1e9` puts the layer
#     somewhere off screen; that it renders is all this claims.
#  3. It covers ONE_SIDED only. A numeric field that is NOT exempted gets the
#     two-ended pydantic probe and no render at all, so a second dead field
#     elsewhere in the tree would not be caught by the reachability check
#     below.
#
# Those three are hand-reasoned, and they are the honest remainder.
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"

# GENERATED, NOT ASSUMED PRESENT. These were bare paths:
#
#     SOURCE = str(FIXTURES / "base6.mp4")
#     LAYER = str(FIXTURES / "square.mp4")
#
# which worked only because both clips happened to be COMMITTED binaries. This
# module never called a generator at all, and nothing noticed, because the files
# were always already there.
#
# Untracking them to satisfy #13 ("every fixture generated idempotently on a
# clean checkout, none committed") exposed it immediately: on a cold checkout
# this file failed 15 of its 16 renders and CREATED NOTHING. Reproduced twice
# in the full suite and once in isolation, so it was the ordering, not a race.
#
# That is the defect the committed binaries were hiding, and it is the reason
# "none committed" was worth landing rather than waiving: a fixture you cannot
# rebuild is a fixture whose absence is a silent failure waiting for the first
# contributor who clones fresh.
if have_ffmpeg():
    SOURCE = str(ensure_long_base_clip().path)
    LAYER = str(ensure_square_clip().path)
else:
    # Every render below is skipped without ffmpeg, so these are never opened.
    SOURCE = str(FIXTURES / "base6.mp4")
    LAYER = str(FIXTURES / "square.mp4")
SOURCE_SECONDS = 6.0
SOURCE_SIZE = (640, 360)
LAYER_SIZE = (480, 480)
SOURCE_RATE = 30.0


def _compile(plan: Plan, out: str) -> list[str]:
    """Compile the way `render` does, with everything it probes supplied.

    NOT AN INCIDENTAL DETAIL. Compiled with less, two of these recipes stop
    testing what they name, and one of them fails in the direction that hands
    out a false pass:

      - `zoom` REFUSES outright without `frame_rate` and `dimensions`, and a
        refusal is an accepted outcome below -- so an exemption on any `Zoom`
        field would have been marked justified by a compiler error that had
        nothing to do with its value.
      - `audio_mix` clamps its start with `min(start, elapsed)`, and `elapsed`
        is 0.0 when no duration is known. Every start then compiles to the same
        `adelay=0`, so the reachability check below would have reported a live
        field as DEAD.

    Both were caught by running it, and both are the same mistake this file is
    about: a check that passes for a reason other than the one it claims.
    """
    return compile_plan(
        plan,
        out,
        durations={SOURCE: SOURCE_SECONDS, LAYER: SOURCE_SECONDS},
        frame_rate=SOURCE_RATE,
        dimensions=SOURCE_SIZE,
        source_sizes={SOURCE: SOURCE_SIZE, LAYER: LAYER_SIZE},
    )


#: A plan that genuinely exercises this field, per field, built explicitly.
#:
#: EXPLICIT FOR THE SAME REASON `SEEDS` IS. A builder clever enough to infer
#: that `Motion.to_x` must be reached through an `Overlay` carrying a `width`
#: and a `height` is a builder that can infer it WRONG, and then render a plan
#: that never touches the field it names -- which is precisely the failure
#: `Stitch.transition_offset` was: a value that never reached the graph,
#: recorded as "rendered rc=0".
RENDER_RECIPES: dict[tuple[str, str], Callable[[float], Any]] = {
    ("Cut", "end"): lambda v: Cut(start=1.0, end=v),
    ("Zoom", "at"): lambda v: Zoom(at=v),
    ("Zoom", "duration"): lambda v: Zoom(duration=v),
    ("Zoom", "to"): lambda v: Zoom(to=v),
    ("Mask", "radius"): lambda v: Overlay(source=LAYER, mask=Mask(kind="rounded_rect", radius=int(v))),
    ("AudioMix", "start"): lambda v: AudioMix(track=SOURCE, start=v),
    ("AudioReplace", "start"): lambda v: AudioReplace(track=SOURCE, start=v),
    ("Trim", "start"): lambda v: Trim(start=v),
    ("Trim", "end"): lambda v: Trim(end=v),
    ("Overlay", "x"): lambda v: Overlay(source=LAYER, x=int(v)),
    ("Overlay", "y"): lambda v: Overlay(source=LAYER, y=int(v)),
    ("Overlay", "end"): lambda v: Overlay(source=LAYER, end=v),
    ("Motion", "to_x"): lambda v: Overlay(source=LAYER, width=320, height=180, motion=Motion(to_x=int(v), to_y=0)),
    ("Motion", "to_y"): lambda v: Overlay(source=LAYER, width=320, height=180, motion=Motion(to_x=0, to_y=int(v))),
    ("Motion", "start"): lambda v: Overlay(
        source=LAYER, width=320, height=180, motion=Motion(to_x=10, to_y=10, start=v)
    ),
    ("Motion", "duration"): lambda v: Overlay(
        source=LAYER, width=320, height=180, motion=Motion(to_x=10, to_y=10, duration=v)
    ),
}

#: The end each entry claims is OPEN -- the opposite of the end it is bounded
#: on, and therefore the value that has to be rendered to justify the exemption.
_OPEN_END = {"below": 1e9, "above": -1e9}


def test_every_exemption_carries_a_way_to_render_it():
    """No entry may be added to ONE_SIDED without the means to check it.

    This is the gate that stops the table drifting back to prose: an exemption
    with no recipe cannot be rendered, so it could only ever be asserted.
    """
    missing = sorted(key for key in ONE_SIDED if key not in RENDER_RECIPES)
    assert not missing, (
        "these ONE_SIDED entries have no RENDER_RECIPES entry, so nothing re-runs the claim "
        "that their open end is open: " + ", ".join(f"{n}.{f}" for n, f in missing)
    )

    stale = sorted(key for key in RENDER_RECIPES if key not in ONE_SIDED)
    assert not stale, "these RENDER_RECIPES entries name a field that is no longer exempted; delete them: " + ", ".join(
        f"{n}.{f}" for n, f in stale
    )


@pytest.mark.parametrize(
    ("name", "field"),
    sorted(ONE_SIDED),
    ids=[f"{n}.{f}" for n, f in sorted(ONE_SIDED)],
)
def test_an_exempted_field_actually_reaches_the_graph(name, field):
    """A field the compiler never reads renders rc=0 at EVERY value.

    `Stitch.transition_offset` sat in this table for exactly that reason: its
    entry read "1e9 rendered rc=0", which was true, and meaningless. The
    compiler derived the offset itself and never looked at the field, so no
    render could ever have contradicted the claim.

    Needs no ffmpeg: it compares the two compiled commands.
    """
    plan = Plan(source=SOURCE)
    ordinary = _compile(plan.with_operation(RENDER_RECIPES[(name, field)](1.0)), "out.mp4")
    altered = _compile(plan.with_operation(RENDER_RECIPES[(name, field)](2.0)), "out.mp4")
    assert ordinary != altered, (
        f"{name}.{field} compiles to the same ffmpeg command at 1.0 and at 2.0, so the value "
        "never reaches the graph. A field the compiler does not read renders rc=0 at every "
        "value, which makes an exemption here unfalsifiable -- wire it up, or delete it."
    )


def _render(plan: Plan, out: Path) -> tuple[int | None, int, int]:
    """(returncode, byte size, stream count). returncode None means it hung."""
    command = _compile(plan, str(out))
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return None, out.stat().st_size if out.exists() else 0, 0
    size = out.stat().st_size if out.exists() else 0
    streams = 0
    if size:
        probed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=nb_streams", "-of", "json", str(out)],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            streams = int(json.loads(probed.stdout)["format"]["nb_streams"])
        except (ValueError, KeyError, json.JSONDecodeError):
            streams = 0
    return done.returncode, size, streams


@pytest.mark.skipif(not have_ffmpeg(), reason="an exemption is justified by a render, so this needs one")
@pytest.mark.parametrize(
    ("name", "field"),
    sorted(ONE_SIDED),
    ids=[f"{n}.{f}" for n, f in sorted(ONE_SIDED)],
)
def test_every_one_sided_exemption_is_re_rendered(name, field, tmp_path):
    """Render the open end. The claim is the render, not the comment beside it."""
    value = _OPEN_END[ONE_SIDED[(name, field)]]
    out = tmp_path / "out.mp4"
    recipe = RENDER_RECIPES[(name, field)]

    # THE RECIPE MUST WORK AT AN ORDINARY VALUE FIRST. Without this, a recipe
    # broken for any unrelated reason -- a missing fixture, a compiler
    # precondition -- raises at the absurd value too, and the refusal branch
    # below reads that as "the tool correctly said no". That is the same
    # false-pass shape as the entries this file exists to stop, one level up.
    # It bit during development: `Zoom` refuses without probe data, so every
    # Zoom exemption briefly "passed" on a compiler error about frame rates.
    _compile(Plan(source=SOURCE).with_operation(recipe(1.0)), str(out))

    refusal = ""
    returncode, size, streams = 0, 0, 1
    try:
        plan = Plan(source=SOURCE).with_operation(recipe(value))
        returncode, size, streams = _render(plan, out)
    except (ValidationError, VidError) as exc:
        refusal = str(exc)

    if refusal:
        # A NAMED REFUSAL IS A LEGITIMATE OUTCOME, and `Trim.start` is why.
        # "Past the end of the file" is not a property of the number, so no
        # bound on the model could express it; `compile.trim` refuses it where
        # the duration is known. The tool said no, by name -- which is the
        # behaviour this whole file exists to produce. The compile above has
        # already established the refusal is about THIS VALUE.
        assert refusal.strip(), f"{name}.{field} was refused with an empty message"
        return

    assert returncode is not None, (
        f"{name}.{field}={value:g} never finished rendering. This table recorded "
        f"`Overlay.start` as open above while 1e9 wedged the render forever at ~250% CPU; a "
        "hang is the worst outcome here, not the mildest, because the caller gets no "
        "diagnostic at all. Bound the field and delete this entry."
    )
    assert returncode == 0, (
        f"{name}.{field}={value:g} is exempted from the upper-bound probe on the grounds that "
        f"it renders, and it does not: ffmpeg exited {returncode}. Either bound the field in "
        "plan.py and delete this entry, or correct the entry."
    )
    assert streams > 0, (
        f"{name}.{field}={value:g} rendered with exit 0 and produced a {size}-byte file "
        "containing NO STREAMS. That is worse than an error: the caller is told the render "
        "succeeded. Refuse the value by name instead."
    )


#: Fields that carry numbers but are deliberately NOT swept here, each with the
#: reason. A dict rather than a set so the reason travels with the entry and
#: cannot rot into an unexplained name.
#:
#: This list is the ONLY sanctioned way for a numeric field to be invisible to
#: the ratchet. Anything else that goes unseen fails
#: `test_no_numeric_field_is_invisible_to_the_ratchet` below.
DOCUMENTED_INVISIBLE: dict[tuple[str, str], str] = {}


def _nested_models(annotation: Any, _depth: int = 0) -> list[type[BaseModel]]:
    """Every plan model reachable through this annotation."""
    if _depth > 6:
        return []
    if isinstance(annotation, type):
        return [annotation] if issubclass(annotation, BaseModel) else []
    found: list[type[BaseModel]] = []
    for arg in get_args(annotation):
        if arg is not type(None):
            found.extend(_nested_models(arg, _depth + 1))
    return found


def _numbers_anywhere(annotation: Any, _depth: int = 0) -> bool:
    """An INDEPENDENT sweep: does a number live anywhere under this annotation?

    DELIBERATELY NOT `_carries_numbers`, and the difference is what stops the
    test below from being a tautology. That one is the ratchet's own discovery
    rule and STOPS at nested models on purpose. This one descends into them,
    so it finds strictly more. The test then demands that everything this finds
    is either swept or named in `DOCUMENTED_INVISIBLE` with a reason.

    Written this way because the defect it guards against is a SPECIAL CASE
    quietly added to the discovery rule. If both sides shared a code path, such
    a case would remove a field from both at once and the test would stay green
    -- which is precisely the failure mode the ratchet exists to prevent, one
    level up.
    """
    if _depth > 6:
        return False
    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return any(_numbers_anywhere(f.annotation, _depth + 1) for f in annotation.model_fields.values())
        return any(annotation is leaf for leaf in NUMERIC_LEAVES)
    args = get_args(annotation)
    return any(_numbers_anywhere(a, _depth + 1) for a in args if a is not type(None))


def test_no_numeric_field_is_invisible_to_the_ratchet() -> None:
    """THE GUARD THE UNSEEN-SPELLINGS FINDING ACTUALLY NEEDED.

    NOTE FOR READERS: this file contains two unrelated counts of four, and
    conflating them wastes a reading. The FOUR MODELS elsewhere here are `Key`,
    `LayerAudio`, `Overlay` and `Motion` -- wrongly certified as guarded by an
    earlier appearance-based proxy. The UNSEEN SPELLINGS below are four ways of
    WRITING a numeric type that the discovery rule could not recognise. Same
    number, different subject, different defect.


    The old discovery rule matched SPELLINGS -- `int`, `float`, and the literal
    strings `"int | None"` and `"float | None"`, plus a string prefix for
    tuples. Four equally ordinary ways of writing those same types were
    invisible to it, each confirmed by injecting a scratch model and watching
    the new-model guard stay green:

        typing.Optional[float]   list[float]   Decimal   int | float

    `Optional[float]` MEANS `float | None`. A contributor writing it in a
    codebase that uses the other spelling would have added an unguarded numeric
    field and seen nothing fail.

    No field on `main` used an unseen spelling when this was found, so the gap
    was latent rather than live. That is exactly why it needs a test: a latent
    gap has no failing instance to keep it honest, and the next contributor is
    the one who finds it. "Nobody would write that" is the same reasoning that
    already failed four times on this branch.
    """
    invisible = []
    swept = _numeric_models()
    for name, obj in vars(plan_module).items():
        if not (inspect.isclass(obj) and issubclass(obj, BaseModel) and obj is not BaseModel):
            continue
        if obj.__module__ != "vid.plan":
            continue
        for field, info in obj.model_fields.items():
            if not _numbers_anywhere(info.annotation):
                continue
            if field in swept.get(name, []):
                continue
            if (name, field) in DOCUMENTED_INVISIBLE:
                continue

            # COMPOSITION IS NOT A GAP -- BUT IT IS VERIFIED, NOT ASSUMED.
            # `Overlay.mask` is `Mask | None`, and Mask is swept as a model in
            # its own right, so its numbers ARE covered; descending would
            # double-report them and make the debt list disagree with itself
            # about who owns a field. The same holds for `Retime.ramp`,
            # `Overlay.motion/audio/key`, and `Plan.operations`.
            #
            # Expressed as a checked RULE rather than a list of names, because
            # the first version of this test DID list names -- and listed only
            # `Retime.ramp`, missing five siblings of the identical kind. An
            # enumeration of exemptions is itself something that goes stale.
            # Only nested models that THEMSELVES carry numbers need sweeping.
            # The first version demanded every nested model be swept and
            # flagged `Plan.operations`, because `Caption`, `AudioRemove`,
            # `Grade` and `Lut` are unswept -- correctly, since they hold only
            # `Literal` and `str`. A model with no numbers has nothing to bound.
            nested = [
                model
                for model in _nested_models(info.annotation)
                if any(_carries_numbers(f.annotation) for f in model.model_fields.values())
            ]
            if nested:
                uncovered = [m.__name__ for m in nested if m.__name__ not in swept]
                if not uncovered:
                    continue
                invisible.append(f"{name}.{field}: nested {uncovered} carry numbers but are never swept")
                continue
            invisible.append(f"{name}.{field}: {info.annotation}")

    assert not invisible, (
        "these fields carry numbers the ratchet cannot see, so they are exempt from every "
        "bound check without appearing in the debt list -- no verdict at all, presented as a "
        "clean one:\n  " + "\n  ".join(invisible)
    )


def test_every_documented_exclusion_still_exists_and_is_still_invisible() -> None:
    """An exemption list that outlives its entries is how debt goes stale.

    If `Retime.ramp` is renamed, or starts being swept directly, its entry here
    must go -- otherwise the list quietly grants an exemption to nothing, and
    the next reader trusts it.
    """
    swept = _numeric_models()
    for (name, field), reason in DOCUMENTED_INVISIBLE.items():
        model = getattr(plan_module, name, None)
        assert model is not None, f"{name} is named in DOCUMENTED_INVISIBLE but no longer exists"
        assert field in model.model_fields, f"{name}.{field} is exempted but no longer exists"
        assert field not in swept.get(name, []), (
            f"{name}.{field} IS swept now, so its exemption is stale and should be deleted. "
            f"It was excluded because: {reason}"
        )


#: The exact spellings the old string-matching discovery could not see, each
#: confirmed invisible on `4ed2c5b` by injecting a scratch model and watching
#: the new-model guard stay green. `float` is the control that WAS caught.
#:
#: These are written as live annotations rather than prose because a comment
#: recording "Decimal is covered" is the kind of claim this whole file exists
#: to stop people making.
SPELLINGS_THAT_MUST_BE_SEEN: dict[str, Any] = {
    "typing.Optional[float]": Optional[float],  # noqa: UP045 -- the point is the OLD spelling
    "list[float]": list[float],
    "Decimal": Decimal,
    "int | float": int | float,
    "float (control)": float,
    "tuple[float, float, float]": tuple[float, float, float],
}


@pytest.mark.parametrize("spelling", list(SPELLINGS_THAT_MUST_BE_SEEN), ids=lambda s: s)
def test_a_numeric_field_is_seen_however_it_is_spelled(spelling: str) -> None:
    """THE TEST A SURVIVING MUTATION DEMANDED.

    Deleting `Decimal` from `NUMERIC_LEAVES` left every other test in this file
    green, because no field in `vid.plan` uses `Decimal` today. The coverage
    was real and untested -- a guard whose failure condition has no instance is
    invisible to every test that only looks at what currently exists.

    So this injects a scratch model carrying one field of each spelling and
    asserts the ratchet discovers it, which is the reviewer's own method for
    proving the gap in the first place, kept as a standing test rather than a
    one-off measurement.

    `Optional[float]` MEANS `float | None`. A contributor writing it in a
    codebase that uses the other spelling would have added an unguarded numeric
    field and seen nothing fail.
    """
    annotation = SPELLINGS_THAT_MUST_BE_SEEN[spelling]
    scratch = type(
        "ScratchNumericModel",
        (BaseModel,),
        {"__annotations__": {"value": annotation}},
    )
    scratch.__module__ = "vid.plan"

    assert _carries_numbers(annotation), (
        f"a field spelled {spelling} carries a number the ratchet cannot see, so it would be "
        "exempt from every bound check without appearing in the debt list"
    )

    # Injected by name at runtime, so the type checker cannot see it -- which
    # is exactly the point: the sweep discovers models by walking `vars()`, and
    # this proves it finds one nothing ever declared.
    plan_module.ScratchNumericModel = scratch  # ty: ignore[unresolved-attribute]
    try:
        assert "ScratchNumericModel" in _numeric_models(), (
            f"a model whose only numeric field is spelled {spelling} is invisible to the sweep"
        )
    finally:
        delattr(plan_module, "ScratchNumericModel")


def test_a_model_with_no_numbers_is_correctly_not_swept() -> None:
    """The other direction: don't sweep what has nothing to bound.

    `Caption`, `AudioRemove`, `Grade` and `Lut` hold only `Literal` and `str`.
    An over-eager predicate that swept them would demand bounds on strings and
    make the debt list meaningless -- and the first draft of
    `test_no_numeric_field_is_invisible_to_the_ratchet` did exactly that,
    flagging `Plan.operations` because those four are unswept.
    """
    swept = _numeric_models()
    for name in ("Caption", "AudioRemove", "Grade", "Lut"):
        model = getattr(plan_module, name)
        assert not any(_carries_numbers(f.annotation) for f in model.model_fields.values()), (
            f"{name} has gained a numeric field and must now be swept"
        )
        assert name not in swept, f"{name} carries no numbers but is being swept"
