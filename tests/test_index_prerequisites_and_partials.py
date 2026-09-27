"""`index` must refuse a missing prerequisite before it works, and never lie about what it wrote.

Both found by `check-spec-adherence` run 2, not by a test.

PREREQUISITE. The manifest declares faster-whisper, and the spec asks that a
missing prerequisite fail immediately. It did not: `transcribe()` discovered
the missing import only after `build()` had already read the duration and run
shot detection -- so a caller without the extra paid for that work, was then
refused, and was left with a brand new index file nobody asked for.

PARTIAL RESULT. Declining the `--vision` spend returned "Nothing described.
The index is unchanged." `build()` writes the index unconditionally
(index.py: `path.write_text`), so on a video with no prior index that sentence
was false at the moment it was printed -- a new file sat on disk. The shots and
speech genuinely succeeded; only vision was declined.
"""

from __future__ import annotations

import importlib.util

import pytest

from tests.fixtures import ensure_clips
from vid import lib
from vid.index import index_path
from vid.schemas import VidError


def test_a_missing_speech_backend_is_refused_before_any_work(monkeypatch) -> None:
    source = str(ensure_clips()["alpha"].path)
    path = index_path(source)
    if path.exists():
        path.unlink()

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "faster_whisper" else real(name, *a, **k),
    )

    with pytest.raises(VidError) as refusal:
        lib.index(source, speech=True)

    message = str(refusal.value)
    assert "vid[speech]" in message, f"the refusal does not name the install: {message!r}"
    assert "--no-speech" in message, "the refusal does not name the reduced capability that works"
    assert not path.exists(), (
        "refusing a missing prerequisite still left a new index on disk -- the whole "
        "point of preflighting before `build()` is that it leaves no new state"
    )


def test_declining_the_vision_spend_does_not_claim_the_index_is_untouched(monkeypatch) -> None:
    source = str(ensure_clips()["alpha"].path)
    path = index_path(source)
    if path.exists():
        path.unlink()

    class Preflights:
        def preflight(self) -> None:
            return None

    monkeypatch.setattr("vid.intelligence.interface.default_intelligence", lambda *a, **k: Preflights())
    monkeypatch.setattr(lib, "_confirm_before_spending", lambda notice, yes: False)

    report = lib.index(source, speech=False, vision=True)

    assert path.exists(), "precondition: build() is expected to have written the index"
    assert "unchanged" not in report, (
        f"the index WAS changed -- {path} exists -- but the result says otherwise: {report!r}"
    )
    assert "skipped" in report, f"the declined part is not named in the result: {report!r}"
