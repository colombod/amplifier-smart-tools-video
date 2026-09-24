"""A JSON plan must be refused on the same terms as a CLI call.

`lib.overlay` validated its arguments; the pydantic models did not. A plan file
reaches `Overlay` and `Motion` directly, never passing through the library
function, so every guard that lived only in `lib` was a guard the JSON door did
not have. An independent review walked through it: a width-only animation the
library refuses was accepted from JSON and rendered horizontally stretched.

These tests assert PARITY rather than each rule separately. A rule added to one
path and not the other is the defect, so the test compares the two doors
against the same payload instead of trusting either alone.
"""

import pydantic
import pytest

from vid import lib
from vid.plan import Plan
from vid.schemas import VidError


def _via_json(**fields) -> bool:
    """True if a JSON plan carrying these overlay fields is ACCEPTED."""
    payload = {
        "plan_format": 1,
        "source": "a.mp4",
        "operations": [dict(op="overlay", source="b.mp4", **fields)],
    }
    try:
        Plan.model_validate(payload)
        return True
    except pydantic.ValidationError:
        return False


def _via_library(**kwargs) -> bool:
    """True if the same intent through `lib.overlay` is ACCEPTED."""
    try:
        lib.overlay(Plan(source="a.mp4"), "b.mp4", **kwargs)
        return True
    except VidError:
        return False


#: (label, library kwargs, equivalent JSON fields). Each is a shape the library
#: has always refused and JSON once accepted.
REFUSED = [
    ("a size on one axis only", {"width": 320}, {"width": 320}),
    (
        "an animation sized on one axis only",
        {"to_width": 1280},
        {"motion": {"to_width": 1280, "start": 0.0, "duration": 1.0}},
    ),
    ("a window that ends before it begins", {"start": "5", "end": "1"}, {"start": 5.0, "end": 1.0}),
    ("an opacity above 1", {"opacity": 1.5}, {"opacity": 1.5}),
    ("an opacity below 0", {"opacity": -3.0}, {"opacity": -3.0}),
]

ACCEPTED = [
    (
        "a fully stated overlay",
        {"width": 320, "height": 180, "opacity": 0.5},
        {"width": 320, "height": 180, "opacity": 0.5},
    ),
    (
        "an animation sized on both axes",
        {"to_width": 1280, "to_height": 720},
        {"motion": {"to_width": 1280, "to_height": 720, "start": 0.0, "duration": 1.0}},
    ),
]


@pytest.mark.parametrize(("label", "kwargs", "fields"), REFUSED, ids=[case[0] for case in REFUSED])
def test_both_doors_refuse_the_same_plan(label, kwargs, fields):
    assert not _via_library(**kwargs), f"the library now ACCEPTS {label}; this test is stale"
    assert not _via_json(**fields), f"a JSON plan still accepts {label}, which the library refuses"


@pytest.mark.parametrize(("label", "kwargs", "fields"), ACCEPTED, ids=[case[0] for case in ACCEPTED])
def test_both_doors_accept_the_same_plan(label, kwargs, fields):
    """Guard against over-correcting: the new refusals must not catch valid plans."""
    assert _via_library(**kwargs), f"the library wrongly refuses {label}"
    assert _via_json(**fields), f"a JSON plan wrongly refuses {label}"
