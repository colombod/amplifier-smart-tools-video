"""A plan that does not validate must fail as a named VidError, not let
pydantic's own ValidationError escape past the CLI's `except VidError`
boundary in `cli.main`.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

from vid.plan import read_plan
from vid.schemas import VidError


def _stdin_of(monkeypatch, payload: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))


def test_a_plan_missing_a_required_field_raises_vid_error_naming_the_remedy(monkeypatch):
    """`cut` has no default for `start`/`end` -- omitting them must not let
    pydantic's ValidationError itself reach the caller."""
    bad_plan = json.dumps({"plan_format": 1, "source": "x.mp4", "operations": [{"op": "cut"}]})
    _stdin_of(monkeypatch, bad_plan)

    with pytest.raises(VidError) as failure:
        read_plan()

    assert not isinstance(failure.value, type(None))
    message = str(failure.value)
    assert "Rebuild it" in message, "a validation failure must name the remedy, not just the defect"


def test_a_plan_with_an_unknown_operation_raises_vid_error(monkeypatch):
    bad_plan = json.dumps({"plan_format": 1, "source": "x.mp4", "operations": [{"op": "levitate"}]})
    _stdin_of(monkeypatch, bad_plan)

    with pytest.raises(VidError):
        read_plan()


def test_a_well_formed_plan_still_reads_normally(monkeypatch):
    """The wrapping must not turn a good plan into a failure."""
    good_plan = json.dumps(
        {
            "plan_format": 1,
            "source": "x.mp4",
            "operations": [{"op": "trim", "start": 1.0, "end": 2.0}],
        }
    )
    _stdin_of(monkeypatch, good_plan)

    plan = read_plan()
    assert plan.source == "x.mp4"
    assert plan.operations[0].op == "trim"
