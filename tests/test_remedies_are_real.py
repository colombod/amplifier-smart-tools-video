"""A remedy must name a setting the tool actually reads, and must survive.

Two deviations from `check-spec-adherence` run 10, and the first of them was
MINE, introduced by the previous commit's sweep:

  1. `voice.ensure_voice` told the caller "That location comes from
     VID_VOICE_DIR" while `voice.py:42` reads `VID_VOICES_DIR`. A remedy
     pointing at a setting the tool ignores is worse than no remedy: the
     caller sets it, nothing changes, and the message says they did it right.
     I wrote that variable name from memory instead of reading it.

  2. `lib.index --vision`, `lib.narrate` and `lib.resolve_transition` caught
     the provider preflight's `VidError` -- which names gh's install
     reference, or `gh auth login` -- and replaced it with "none is
     configured". The caller was told to configure a provider they had
     configured, and the one line that would have fixed it was discarded.

Both are the same failure as the false ONE_SIDED entries: a sentence that
reads as help and is not true. So both are enumerated here rather than
spot-checked.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from vid.schemas import VidError

SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "src" / "vid"


def _env_vars() -> tuple[set[str], dict[str, list[str]]]:
    read: set[str] = set()
    named: dict[str, list[str]] = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        read.update(m.group(1) for m in re.finditer(r'environ\.get\(\s*"(VID_[A-Z_]+)"', text))
        for number, line in enumerate(text.split("\n"), 1):
            if "environ.get" in line:
                continue
            for match in re.finditer(r"\b(VID_[A-Z_]+)\b", line):
                named.setdefault(match.group(1), []).append(f"{path.relative_to(SOURCE_ROOT.parent)}:{number}")
    return read, named


def test_no_message_names_an_environment_variable_the_tool_ignores() -> None:
    """The whole class, not just the one instance a checker happened to cite."""
    read, named = _env_vars()
    phantom = {name: sites for name, sites in named.items() if name not in read}

    assert not phantom, (
        "these environment variables are named in messages or comments but are never read by "
        "the code, so a caller following the remedy changes nothing: "
        + "; ".join(f"{name} at {', '.join(sites)}" for name, sites in sorted(phantom.items()))
    )


def test_the_detector_would_notice_a_phantom_variable() -> None:
    """The enumeration must be able to fail, or it certifies nothing."""
    read, _ = _env_vars()

    assert "VID_VOICES_DIR" in read, "the detector is not finding variables the code demonstrably reads"
    assert "VID_VOICE_DIR" not in read, (
        "VID_VOICE_DIR is now read by the code; the regression this guards against has changed shape"
    )


def test_the_voice_cache_remedy_names_the_variable_that_is_read() -> None:
    """The specific instance, pinned at the message rather than by grep."""
    source = (SOURCE_ROOT / "voice.py").read_text(encoding="utf-8")

    assert "VID_VOICES_DIR when it is set" in source
    assert "VID_VOICE_DIR when it is set" not in source


# ---------------------------------------------------------------------------
# A precise provider diagnosis must not be collapsed into a generic one.
# ---------------------------------------------------------------------------

PRECISE = "Model-backed capabilities need the GitHub CLI. Install it (https://cli.github.com/) and sign in."


@pytest.fixture
def provider_refuses(monkeypatch):
    """`default_intelligence().preflight()` raising the real gh diagnosis."""
    import vid.intelligence.interface as interface

    class _Refusing:
        def preflight(self) -> None:
            raise VidError(PRECISE)

    monkeypatch.setattr(interface, "default_intelligence", lambda: _Refusing())


def test_index_vision_keeps_the_providers_own_diagnosis(monkeypatch, tmp_path, provider_refuses) -> None:
    from vid import lib
    import vid.index as index

    monkeypatch.setattr(index, "load", lambda _v: None)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"stand-in")

    with pytest.raises(VidError) as failure:
        lib.index(str(video), speech=False, vision=True, yes=True)

    assert str(failure.value) == PRECISE, (
        f"the provider's precise remedy was replaced by a generic one: {str(failure.value)!r}"
    )


def test_narrate_keeps_the_providers_own_diagnosis(monkeypatch, tmp_path, provider_refuses) -> None:
    from vid import lib
    import vid.index as index
    import vid.probe as probe
    import vid.speech.interface as speech

    monkeypatch.setattr(index, "load", lambda _v: {"duration": 5.0, "shots": [], "speech": []})
    monkeypatch.setattr(probe, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(speech, "speech_preflight", lambda _voice: None)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"stand-in")

    with pytest.raises(VidError) as failure:
        lib.narrate(str(video), "explain the product")

    assert str(failure.value) == PRECISE, (
        f"the provider's precise remedy was replaced by a generic one: {str(failure.value)!r}"
    )


def test_find_still_degrades_to_literal_search_without_a_provider() -> None:
    """The correction must NOT have been applied where a model is optional.

    `find` documents literal search as needing no provider, and it calls the
    same preflight unconditionally. Re-raising there would break literal
    search on any machine without gh -- which is why this class was fixed
    site by site rather than with one blanket rule.
    """
    source = (SOURCE_ROOT / "lib.py").read_text(encoding="utf-8")
    find_body = source[source.index("def find(") :]
    find_body = find_body[: find_body.index("\ndef ")]

    assert "except VidError:" not in find_body, (
        "find now re-raises a provider refusal, so literal search -- which needs no provider -- "
        "would fail on a machine without one"
    )


# ---------------------------------------------------------------------------
# Run 11: a provider profile whose `api_key_env` is present but unusable, and
# a credential the code preflights that the manifest never declared.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "described"),
    [('["OPENAI_API_KEY"]', "list"), ('{ name = "X" }', "dict"), ("3", "int"), ("true", "bool")],
    ids=["list", "table", "int", "bool"],
)
def test_a_non_string_api_key_env_is_refused_by_name(monkeypatch, tmp_path, value, described) -> None:
    """PRESENT IS NOT USABLE, and the old check only tested presence.

    The value goes straight to `os.environ.get`, which requires a str, so a
    TOML list -- an easy thing to write -- died downstream as a raw
    `TypeError: str expected, not list` that `cli.main` does not translate.
    Reproduced exactly before the guard existed.
    """
    from vid.config import provider_profile

    config = tmp_path / "vid" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(f"[providers.openai.work]\napi_key_env = {value}\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    with pytest.raises(VidError) as failure:
        provider_profile("openai", "work", default_env_var="OPENAI_API_KEY")

    message = str(failure.value)
    assert described in message, f"the failure does not name what was found: {message!r}"
    assert "api_key_env" in message, f"the failure does not name the field: {message!r}"
    assert str(config) in message, f"the failure does not name the file: {message!r}"


def test_a_string_api_key_env_still_works(monkeypatch, tmp_path) -> None:
    """The guard must not have started refusing valid configuration."""
    from vid.config import provider_profile

    config = tmp_path / "vid" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('[providers.openai.work]\napi_key_env = "MY_KEY"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert provider_profile("openai", "work")["api_key_env"] == "MY_KEY"


def test_the_manifest_declares_the_credential_the_code_preflights() -> None:
    """`speech.interface` refuses on a missing API key; the manifest must say so.

    The spec requires the failure and the manifest to agree. The code
    preflighted this credential while the manifest declared only the PACKAGE
    extra -- installing `openai` and having an account are different
    prerequisites that fail at different moments.
    """
    from vid.core.manifest import load_manifest, manifest_install

    names = {requirement.name for requirement in load_manifest().requires}
    assert "openai-api-key" in names, f"the preflighted credential is not declared: {sorted(names)}"
    assert manifest_install("openai-api-key"), "the credential is declared with no provisioning reference"

    helper = (SOURCE_ROOT / "speech" / "openai_tts.py").read_text(encoding="utf-8")
    assert "manifest_install('openai-api-key')" in helper, (
        "the missing-key refusal does not carry the manifest's provisioning reference"
    )


def test_a_vision_failure_says_the_basic_index_was_kept(monkeypatch, tmp_path) -> None:
    """Run 12: the DECLINE path reported the retained index; the FAILURE path did not.

    `build()` writes the shots-and-speech index unconditionally, so when
    `describe()` raises, that expensive work is already on disk. The error said
    only "None were applied -- re-run", and a caller reading that reasonably
    concludes nothing happened and re-runs the shot detection and
    transcription they had already paid for.
    """
    from vid import lib
    import vid.index as index
    import vid.intelligence.interface as interface
    import vid.probe as probe

    monkeypatch.setenv("VID_INDEX_DIR", str(tmp_path / "index-store"))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"stand-in bytes")

    record = {"video": str(video), "duration": 5.0, "shots": [{"id": "s0", "start": 0.0, "end": 5.0}], "speech": []}

    class _Intelligence:
        def preflight(self) -> None:
            return None

    def _describe_fails(*args, **kwargs):
        raise VidError("The model described 0 of 1 frame(s). None were applied.")

    monkeypatch.setattr(probe, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(probe, "have_ffprobe", lambda: True)
    monkeypatch.setattr(index, "build", lambda *a, **k: record)
    monkeypatch.setattr(index, "describe", _describe_fails)
    monkeypatch.setattr(interface, "default_intelligence", lambda: _Intelligence())

    with pytest.raises(VidError) as failure:
        lib.index(str(video), speech=False, vision=True, yes=True)

    message = str(failure.value)
    assert "None were applied" in message, f"the vision diagnosis was discarded: {message!r}"
    assert "WAS saved" in message, f"the failure does not say the index survived: {message!r}"
    assert str(index.index_path(str(video))) in message, f"the failure does not name where: {message!r}"
    assert "--vision --yes" in message, f"the failure does not name how to resume: {message!r}"


def test_both_missing_key_refusals_are_the_same_sentence() -> None:
    """Run 13: the preflight carried the manifest reference and the backend did not.

    `speech_preflight` and `OpenAIBackend.__init__` both check for the key, so
    the sentence was written twice -- and when the first was corrected the
    second kept telling the caller to export a key without saying where one
    comes from. Both now call `missing_api_key_error`, so there is one
    sentence to be right about.
    """
    from vid.core.manifest import manifest_install
    from vid.speech.openai_tts import missing_api_key_error

    produced = str(missing_api_key_error("nova", "work", "MY_KEY"))
    assert manifest_install("openai-api-key") in produced
    assert "MY_KEY" in produced
    assert "work" in produced
    assert "nova" in produced

    # Composed in exactly ONE place tree-wide -- the helper itself. A second
    # site writing its own copy is how the two drifted apart.
    sites = [
        f"{path.relative_to(SOURCE_ROOT.parent)}:{number}"
        for path in SOURCE_ROOT.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1)
        if "needs {env_var} set" in line or "needs {self._env_var} set" in line
    ]
    assert len(sites) == 1, f"the missing-key sentence is composed in {len(sites)} places: {sites}"


def test_a_sourceless_recolor_names_how_to_supply_a_source() -> None:
    """Run 14: the message stated the condition and no corrective action.

    `recolor` measures the SOURCE's palette, so a sourceless plan cannot be
    recoloured -- but "needs a plan with a source video" left the caller to
    work out how a plan acquires one. Its sibling in `compile.py` already said
    "Name one when the chain starts".
    """
    from vid import lib
    from vid.plan import Plan

    with pytest.raises(VidError) as failure:
        lib.recolor(Plan(), like="reference.png")

    message = str(failure.value)
    assert "Start the chain by naming a video" in message, f"no corrective action given: {message!r}"
    assert "vid recolor" in message, f"the corrective invocation is not shown: {message!r}"
