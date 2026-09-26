"""A model that answers SOME segments has not written the narration.

Found by `check-spec-adherence` run 4 (`partial-results-are-failures`), and
nothing in the suite could have caught it: every other narrate test
monkeypatches `write_script` away, so the real parser was never driven.

`written.get(i, "")` gave a segment the model never mentioned the same empty
text as one the model deliberately marked `-`. `fit` then stamps every empty
line `fitted = True, note = "left silent"`, so `refuse_unfitted` -- the guard
whose entire job is to stop a narration succeeding when it did not fit --
could not see them either. A model answering 2 of 20 segments produced a
SUCCESSFUL narration, silent for eighteen, each one reported as intentional.

The prompt asks for one numbered line per segment and documents `N: -` as the
way to say nothing, so an absent ID is an incomplete answer, not a quiet one.
"""

from __future__ import annotations

import pytest

from vid.narrate import slots_from, write_script
from vid.schemas import VidError

RECORD = {
    "video": "talk.mp4",
    "duration": 30.0,
    "shots": [{"id": f"s{i}", "start": i * 5.0, "end": i * 5.0 + 5.0} for i in range(6)],
    "speech": [],
}


class Intelligence:
    def preflight(self) -> None:
        return None


def _reply(monkeypatch, text: str) -> None:
    monkeypatch.setattr("vid.intelligence.ask", lambda *a, **k: text)


def test_a_model_that_omits_segments_is_refused(monkeypatch) -> None:
    total = len(slots_from(RECORD))
    assert total >= 3, "fixture must produce enough segments for an omission to matter"

    # Answers only the first two of however many segments exist.
    _reply(monkeypatch, "0: First line.\n1: Second line.")

    with pytest.raises(VidError) as refusal:
        write_script(RECORD, "explain the product", Intelligence())

    message = str(refusal.value)
    assert f"{total}" in message, f"the refusal does not say how many were asked for: {message!r}"
    assert "omitting" in message, f"the refusal does not name the omission: {message!r}"
    assert "`N: -`" in message or "N: -" in message, (
        "the refusal does not tell the caller how to express deliberate silence"
    )


@pytest.mark.parametrize(
    ("label", "tail"),
    [("nothing at all", ""), ("whitespace only", "    "), ("a tab", "\t")],
)
def test_a_segment_answered_emptily_is_refused(monkeypatch, label, tail) -> None:
    """`3:` is not an answer, and the omission guard above cannot see it.

    THE SIBLING THE OMISSION FIX MISSED. That fix keyed on the index being
    ABSENT from `written`. `3:` puts the index IN the dict with an empty tail:
    it walks past the missing-index check, becomes `text = ""`, and `fit`
    stamps it `fitted = True, note = "left silent"` -- indistinguishable from
    the model deliberately writing `3: -`.

    Driven through the REAL parser, with the exact strings that produce it.
    The previous sweep for siblings grepped for patterns across files instead,
    and this is what grepping missed.
    """
    total = len(slots_from(RECORD))
    lines = [f"{i}: Line {i}." for i in range(total)]
    lines[3] = f"3:{tail}"
    _reply(monkeypatch, "\n".join(lines))

    with pytest.raises(VidError) as refusal:
        write_script(RECORD, "explain the product", Intelligence())

    message = str(refusal.value)
    assert "blank" in message, f"the refusal does not name the blank answer ({label}): {message!r}"
    assert "3" in message, f"the refusal does not say which segment was blank: {message!r}"
    assert "N: -" in message, "the refusal does not tell the caller how to express deliberate silence"


def test_a_duplicate_blank_index_cannot_erase_a_real_answer(monkeypatch) -> None:
    """`written` is a dict, so a later `3:` overwrites an earlier `3: text`.

    Checking the STORED value rather than the input lines is what catches this;
    a check that counted answered lines would see 7 answers for 6 segments and
    conclude everything was fine.
    """
    total = len(slots_from(RECORD))
    lines = [f"{i}: Line {i}." for i in range(total)]
    lines.append("3:")
    _reply(monkeypatch, "\n".join(lines))

    with pytest.raises(VidError) as refusal:
        write_script(RECORD, "explain the product", Intelligence())

    assert "blank" in str(refusal.value)


def test_deliberate_silence_is_still_honoured(monkeypatch) -> None:
    """The `-` marker must keep working -- refusing it would be the over-correction."""
    total = len(slots_from(RECORD))
    _reply(monkeypatch, "\n".join(f"{i}: -" if i % 2 else f"{i}: Line {i}." for i in range(total)))

    script = write_script(RECORD, "explain the product", Intelligence())

    assert len(script.lines) == total
    silent = [line for line in script.lines if not line.text]
    assert silent, "the `-` markers did not produce silent lines"
    assert len(silent) < total, "every line came back silent -- the fixture proves nothing"
