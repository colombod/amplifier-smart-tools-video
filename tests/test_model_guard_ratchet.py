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

import inspect

from pydantic import BaseModel, ValidationError
import pytest

import vid.plan as plan_module

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
    "AudioMix": {"level", "start"},
    "AudioReplace": {"start"},
    "Key": {"blend", "similarity", "threshold"},
    "LayerAudio": {"base_gain_db", "gain_db"},
    "Motion": {"duration", "start", "to_x", "to_y"},
    "Overlay": {"end", "start", "x", "y"},
    "Plan": {"plan_format"},
    "RampPoint": {"at", "speed"},
    # The four colour statistics were INVISIBLE to the sweep until the tuple
    # spelling was added -- twelve floats nothing was looking at. They are
    # normally computed by `recolor` itself, but a JSON plan can set them.
    "Recolor": {"reference_mean", "reference_std", "source_mean", "source_std", "strength"},
    "Retime": {"speed"},
    "Stitch": {"transition_duration", "transition_offset"},
    "Trim": {"end", "start"},
    "Vignette": {"strength"},
}

#: Fully guarded, by measurement: Cut, Mask, Zoom.
#:
#: This list GREW when the one-sided rule started being executed. `AudioMix`,
#: `Motion`, `AudioReplace` and `Retime` gained entries that the old `any`
#: reported as guarded because each refused one end. That is the bug the round-7
#: review named, and the growth is the honest measurement of it.


def _is_numeric_tuple(annotation) -> bool:
    """A fixed-width tuple of numbers, e.g. `tuple[float, float, float]`."""
    text = str(annotation)
    return text.startswith("tuple[") and all(part.strip() in ("int", "float") for part in text[6:-1].split(","))


def _numeric_models() -> dict[str, list[str]]:
    """Plan models carrying int/float fields, and which fields those are."""
    found = {}
    for name, obj in vars(plan_module).items():
        if not (inspect.isclass(obj) and issubclass(obj, BaseModel) and obj is not BaseModel):
            continue
        if obj.__module__ != "vid.plan":
            continue
        # FOUR SPELLINGS PLUS TUPLES. The scalar list misses a numeric tuple,
        # and `Recolor`'s four `tuple[float, float, float]` colour statistics
        # carry twelve floats that nothing was looking at. `Retime.ramp` is
        # `list[RampPoint]` and needs no special case: RampPoint is swept as a
        # model in its own right, so its numbers are already covered.
        numeric = [
            field
            for field, info in obj.model_fields.items()
            if info.annotation in (int, float)
            or str(info.annotation) in ("int | None", "float | None")
            or _is_numeric_tuple(info.annotation)
        ]
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
ONE_SIDED: dict[tuple[str, str], str] = {
    ("Cut", "end"): "below",
    ("Zoom", "at"): "below",
    ("Zoom", "duration"): "below",
    # Both render clean at 1e9 against a 640x360 base: radius clamps to the
    # shape, and a zoom factor is open above by definition.
    ("Mask", "radius"): "below",
    ("Zoom", "to"): "below",
    # NOT `Mask.feather`. It looked identical to these two and is not: at 1e9
    # ffmpeg failed the render outright, rc=222 "Numerical result out of
    # range". It is now bounded at 1024 in plan.py and stays two-sided here.
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
    if _is_numeric_tuple(annotation):
        # One absurd element among valid ones: a guard that checks only the
        # first slot must not read as guarding the whole tuple.
        width = str(annotation)[6:-1].count(",") + 1
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
    ]
)


@pytest.mark.parametrize(("name", "field"), GUARDED_BY_THIS_BRANCH, ids=[f"{n}.{f}" for n, f in GUARDED_BY_THIS_BRANCH])
def test_the_fields_this_branch_guarded_stay_guarded(name, field):
    """Pinned per FIELD, so a refactor cannot drop one back to unchecked while
    the model keeps a validator for something else and still looks guarded."""
    assert field not in _unguarded_fields(name), (
        f"{name}.{field} now accepts ±1e9; a JSON plan can set it unchecked again"
    )
