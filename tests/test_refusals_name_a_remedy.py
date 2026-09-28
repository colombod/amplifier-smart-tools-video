"""A refusal has to say what to do instead, not only what was wrong.

FOUND BY AUDIT, not by a failing test. Reviewing every validator added with
the overlay surface against "does this name a remedy", three of them named
none at all:

    A zoom factor cannot be negative, and -2 is.
    A zoom cannot be centred before the file starts, and -5 is negative.
    A rounded rectangle's radius cannot be negative, and -1 is.

and two named a CONSEQUENCE, which reads like a remedy and is not:

    A cut must not end before it starts, and 5 -> 1 does. A reversed range
    removes nothing and duplicates footage instead.

That tells the caller why it matters. It does not tell them to swap the two
numbers. The sibling validators got this right by stating a RANGE -- "must be
between 0.01x and 100x" tells you what to pass -- so the gap was uneven rather
than absent by policy.

WHY THIS IS A TABLE OF EXACT PHRASES AND NOT A KEYWORD SCAN. The audit's first
pass looked for words like "must", "use" or "between" and reported the Cut
message as compliant, because "must" appears in "must not end before it
starts" -- the statement of the RULE, not of the remedy. A scan for
remedy-shaped words marks the rule itself as its own fix. So each row below
names the specific instruction that message has to carry, and a message that
loses it fails by name.
"""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from vid.plan import Cut, Mask, Zoom


def _refusal(make) -> str:
    with pytest.raises(ValidationError) as caught:
        make()
    return " ".join(str(error.get("msg", "")) for error in caught.value.errors())


#: (label, how to trigger it, the instruction the message must carry)
REMEDIES = [
    ("cut, reversed range", lambda: Cut(start="5", end="1"), "Swap them: cut 1 -> 5."),
    ("zoom, negative factor", lambda: Zoom(to=-2), "Give a positive factor"),
    ("zoom, negative duration", lambda: Zoom(duration=-1), "Give a duration of 0 or more seconds"),
    ("zoom, negative centre", lambda: Zoom(at=-5), "Give a time of 0 or more seconds"),
    ("rounded rect, negative radius", lambda: Mask(kind="rounded_rect", radius=-1), "Give a radius of 0 or more"),
]


@pytest.mark.parametrize(("label", "make", "remedy"), REMEDIES, ids=[row[0] for row in REMEDIES])
def test_the_refusal_names_what_to_do_instead(label: str, make, remedy: str) -> None:
    message = _refusal(make)
    assert remedy in message, (
        f"the {label} refusal no longer tells the caller what to do. It said:\n    {message}\n"
        f"which is missing {remedy!r}. Stating the rule and its consequence is not a remedy -- "
        "the caller still has to guess the fix."
    )


@pytest.mark.parametrize(("label", "make", "remedy"), REMEDIES, ids=[row[0] for row in REMEDIES])
def test_the_refusal_still_says_what_was_wrong_and_what_was_given(label: str, make, remedy: str) -> None:
    """The remedy must be ADDED to the diagnosis, never replace it.

    A message that says only "give a positive factor" has lost the offending
    value, which is what makes a refusal diagnosable in a piped chain where the
    caller cannot see the plan.
    """
    message = _refusal(make)
    assert "cannot" in message or "must not" in message, (
        f"the {label} refusal no longer states the rule it enforces: {message}"
    )
    assert any(token in message for token in ("-1", "-2", "-5", "5 -> 1")), (
        f"the {label} refusal no longer quotes the value it rejected: {message}"
    )
