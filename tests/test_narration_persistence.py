"""A narration that synthesised but could not be saved must say so, by name.

Deviation `failure-names-remedy`, found by `check-spec-adherence` run 6 at
`lib.py:832-835`: the durable-directory `mkdir` and the track `copyfile` were
unguarded, so an unwritable `VID_NARRATION_DIR` escaped as a raw `OSError`
traceback. `cli.main` translates only `VidError`, so that reached the caller as
a stack trace naming neither the setting that chose the location nor a remedy.

THE FIFTH SIBLING of a class fixed four times on this branch -- `index.save`,
`index.load`, and `fingerprint`'s three filesystem calls. The class was swept
one reported instance at a time; this is the instance nobody reported.

WORSE HERE THAN IN THE INDEX, which is why it earns its own module: the model
call and the speech synthesis have ALREADY happened by this line. A traceback
here discards completed, billed work and tells the caller nothing about how to
keep it.

THE REFUSAL IS A REAL ONE. The narration directory is pointed beneath a regular
FILE, so `mkdir(parents=True)` raises `NotADirectoryError` from the actual
filesystem -- no patched `pathlib`, and it behaves the same as root, unlike a
`chmod 000` test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vid import lib
from vid.narrate import Line, Script
from vid.schemas import VidError


class _Speaker:
    def say(self, text: str, path: Path):  # pragma: no cover - never reached
        raise AssertionError("fit is substituted; the speaker is never called")


@pytest.fixture
def narration_reaches_the_copy(monkeypatch, tmp_path):
    """Every seam between `narrate`'s entry and the persistence it guards.

    Substituted because the branch under test sits AFTER a model call and a
    speech synthesis, and neither belongs in a unit test. Nothing here stands
    in for the code being tested: the filesystem refusal is real, and the
    guard translating it is the real one.
    """
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"stand-in bytes -- fingerprint() only needs a real file to hash")

    script = Script(
        source=str(video),
        prompt="explain the product",
        lines=[Line(index=0, start=0.0, budget=5.0, text="A line.", fitted=True)],
    )

    def _assemble(_script, path, _total):
        Path(path).write_bytes(b"RIFF....WAVE synthesised narration")
        return path

    # PATCHED ON THEIR SOURCE MODULES, because `narrate` imports each name
    # inside the function body rather than at module scope -- patching
    # `vid.lib` would bind nothing and the real one would still run.
    import vid.index as index
    import vid.intelligence.interface as interface
    import vid.narrate as narrate_module
    import vid.probe as probe
    import vid.speech.interface as speech

    class _Intelligence:
        def preflight(self) -> None:
            return None

    monkeypatch.setattr(index, "load", lambda _v: {"duration": 5.0, "shots": [], "speech": []})
    monkeypatch.setattr(probe, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(speech, "speech_preflight", lambda _voice: None)
    monkeypatch.setattr(speech, "resolve_speech", lambda _voice: _Speaker())
    monkeypatch.setattr(narrate_module, "write_script", lambda *a, **k: script)
    monkeypatch.setattr(narrate_module, "fit", lambda *a, **k: None)
    monkeypatch.setattr(narrate_module, "assemble", _assemble)
    monkeypatch.setattr(narrate_module, "refuse_unfitted", lambda *a, **k: None)
    monkeypatch.setattr(interface, "default_intelligence", lambda: _Intelligence())
    return str(video)


def test_an_unwritable_narration_dir_names_the_setting_not_a_traceback(
    monkeypatch, tmp_path, narration_reaches_the_copy
):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a regular file, so mkdir beneath it genuinely fails")
    monkeypatch.setenv("VID_NARRATION_DIR", str(blocker / "narration"))

    with pytest.raises(VidError) as failure:
        lib.narrate(narration_reaches_the_copy, "explain the product")

    message = str(failure.value)
    assert "VID_NARRATION_DIR" in message, f"the failure must name the setting: {message!r}"
    assert "--out" in message, f"the failure must offer the other way to keep the track: {message!r}"
    assert "synthesised" in message, f"the failure must say the work was DONE, not that narration failed: {message!r}"


def test_a_writable_narration_dir_still_returns_the_track(monkeypatch, tmp_path, narration_reaches_the_copy):
    """The guard must not have started refusing a working installation."""
    destination = tmp_path / "narration-store"
    monkeypatch.setenv("VID_NARRATION_DIR", str(destination))

    report = lib.narrate(narration_reaches_the_copy, "explain the product")

    written = list(destination.glob("*.wav"))
    assert written, "no narration track was persisted"
    assert str(written[0]) in report, "the report does not name the track it wrote"
