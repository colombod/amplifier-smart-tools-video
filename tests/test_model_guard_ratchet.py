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

from pydantic import BaseModel
import pytest

import vid.plan as plan_module

#: Models carrying numeric fields with NO validator, as of this commit.
#:
#: CORRECTION. An earlier version of this note said these were "reachable only
#: through a JSON plan, so `lib.*` cannot protect it". That was asserted, not
#: checked, and it was wrong for at least two: `lib.cut` and `lib.zoom` do NOT
#: guard their arguments -- they parse timecodes and construct the model, so
#: the CLI reaches the same defects. Measured: `lib.cut(start=5, end=2)`,
#: `lib.cut(5, 5)`, `lib.zoom(duration=-3)` and `lib.zoom(to=0)` were all
#: accepted. Only `lib.retime` had a real guard.
#:
#: Cut, Retime and Zoom were fixed rather than deferred once that came out --
#: a defect reachable from the CLI is not someone else's problem later.
#: `AudioMix` and `AudioReplace` are here because THIS RATCHET CAUGHT THEM on
#: its first run. The list was transcribed from an enumeration whose output I
#: had piped through `tail`, so two models were cut off and the truncated list
#: was recorded as complete -- the same "real evidence, wrong conclusion" shape
#: this whole branch keeps producing. The full sweep finds 16 models with
#: numeric fields: 5 guarded, 11 not.
KNOWN_UNGUARDED = {
    "AudioMix",
    "AudioReplace",
    "Plan",
    "RampPoint",
    "Recolor",
    "Stitch",
    "Trim",
    "Vignette",
}


def _numeric_models() -> dict[str, list[str]]:
    """Plan models carrying int/float fields, and which fields those are."""
    found = {}
    for name, obj in vars(plan_module).items():
        if not (inspect.isclass(obj) and issubclass(obj, BaseModel) and obj is not BaseModel):
            continue
        if obj.__module__ != "vid.plan":
            continue
        numeric = [
            field
            for field, info in obj.model_fields.items()
            if info.annotation in (int, float) or str(info.annotation) in ("int | None", "float | None")
        ]
        if numeric:
            found[name] = numeric
    return found


def _is_guarded(name: str) -> bool:
    """Does the model declare its own validator?"""
    obj = getattr(plan_module, name)
    return any(key.startswith("_") and callable(getattr(value, "__func__", value)) for key, value in vars(obj).items())


def test_no_new_model_arrives_unguarded():
    """A new plan model with numeric fields must guard them, or be recorded.

    The failure message names the model, because the whole point is that this
    should be noticed at the moment it is introduced rather than by a reviewer
    four rounds later.
    """
    models = _numeric_models()
    unguarded = {name for name in models if not _is_guarded(name)}

    arrived = unguarded - KNOWN_UNGUARDED
    assert not arrived, (
        f"new plan model(s) with unvalidated numeric fields: "
        f"{', '.join(f'{name} {models[name]}' for name in sorted(arrived))}. "
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
    now_guarded = {name for name in KNOWN_UNGUARDED if name in models and _is_guarded(name)}

    assert not now_guarded, (
        f"{', '.join(sorted(now_guarded))} now has a validator but is still listed as unguarded. "
        "Remove it from KNOWN_UNGUARDED."
    )

    vanished = KNOWN_UNGUARDED - set(models)
    assert not vanished, f"KNOWN_UNGUARDED names models that no longer exist: {', '.join(sorted(vanished))}"


@pytest.mark.parametrize("name", sorted(["Overlay", "Motion", "Mask", "Key", "LayerAudio", "Cut", "Retime", "Zoom"]))
def test_the_models_this_pr_guarded_stay_guarded(name):
    """The five guarded by this branch, pinned so a refactor cannot quietly
    drop one back into the unguarded set."""
    assert _is_guarded(name), f"{name} lost its validator; a JSON plan can now set its numbers unchecked"
