"""Contract tests for the pluggable speech backend.

Piper stays the default and is never touched by this file (`vid.voice` is the
thing `vid.speech.piper` wraps). OpenAI is an explicit alternative, reached
only through `--voice openai:<voice>[@profile]` -- these tests exist mostly to
prove that prefix is the ONLY door in: piper being unavailable never falls
back to it, and every refusal along the way names a remedy without leaking
key material.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
import json
from pathlib import Path

import pytest

from vid.config import config_path, provider_profile
from vid.schemas import VidError
from vid.speech.interface import NO_SPEECH_BACKEND_HINT, parse_voice_spec, speech_preflight
from vid.speech.openai_tts import DEFAULT_OPENAI_VOICE
from vid.voice import DEFAULT_VOICE

OPENAI_INSTALLED = importlib.util.find_spec("openai") is not None

# ---------------------------------------------------------------------------
# 1. parse_voice_spec -- the exhaustive grammar table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("voice", "expected"),
    [
        (None, ("piper", DEFAULT_VOICE, None)),
        ("", ("piper", DEFAULT_VOICE, None)),
        ("en_US-lessac-medium", ("piper", "en_US-lessac-medium", None)),
        ("openai:alloy", ("openai", "alloy", "default")),
        ("openai:alloy@work", ("openai", "alloy", "work")),
        ("openai:", ("openai", DEFAULT_OPENAI_VOICE, "default")),
        ("openai:@work", ("openai", DEFAULT_OPENAI_VOICE, "work")),
    ],
)
def test_parse_voice_spec_matches_the_grammar_table(voice, expected) -> None:
    assert parse_voice_spec(voice) == expected


def test_parse_voice_spec_refuses_an_unknown_prefix_naming_both_valid_forms() -> None:
    with pytest.raises(VidError) as failure:
        parse_voice_spec("elevenlabs:rachel")

    message = str(failure.value)
    assert "openai:<voice>" in message
    assert DEFAULT_VOICE in message


def test_parse_voice_spec_is_an_exact_match_no_case_folding() -> None:
    with pytest.raises(VidError):
        parse_voice_spec("OpenAI:alloy")


# ---------------------------------------------------------------------------
# 2. Config: precedence, VID_CONFIG override, missing profile, malformed TOML,
#    and the raw `api_key` hard refusal. Real temp files throughout.
# ---------------------------------------------------------------------------


def test_config_path_honours_vid_config_over_everything_else(monkeypatch, tmp_path) -> None:
    custom = tmp_path / "somewhere" / "custom.toml"
    monkeypatch.setenv("VID_CONFIG", str(custom))

    assert config_path() == custom


def test_config_path_falls_back_to_xdg_config_home(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("VID_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert config_path() == tmp_path / "vid" / "config.toml"


def test_zero_config_default_profile_synthesises_the_implicit_env_var(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VID_CONFIG", str(tmp_path / "does-not-exist.toml"))

    section = provider_profile("openai", "default", default_env_var="OPENAI_API_KEY")

    assert section == {"api_key_env": "OPENAI_API_KEY"}


def test_no_config_file_with_a_named_non_default_profile_names_the_path(monkeypatch, tmp_path) -> None:
    missing = tmp_path / "does-not-exist.toml"
    monkeypatch.setenv("VID_CONFIG", str(missing))

    with pytest.raises(VidError, match="work"):
        provider_profile("openai", "work", default_env_var="OPENAI_API_KEY")


def test_a_profile_missing_from_an_existing_file_lists_the_defined_profiles(monkeypatch, tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[providers.openai.work]\napi_key_env = "WORK_OPENAI_KEY"\n', encoding="utf-8")
    monkeypatch.setenv("VID_CONFIG", str(config))

    with pytest.raises(VidError) as failure:
        provider_profile("openai", "default", default_env_var="OPENAI_API_KEY")

    assert "work" in str(failure.value)


def test_malformed_toml_names_the_path_and_the_parse_problem(monkeypatch, tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("this is not valid toml [[[", encoding="utf-8")
    monkeypatch.setenv("VID_CONFIG", str(config))

    with pytest.raises(VidError) as failure:
        provider_profile("openai", "default", default_env_var="OPENAI_API_KEY")

    message = str(failure.value)
    assert str(config) in message


def test_a_raw_api_key_anywhere_under_providers_is_a_hard_refusal(monkeypatch, tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[providers.openai.default]\napi_key = "sk-super-secret-value"\n', encoding="utf-8")
    monkeypatch.setenv("VID_CONFIG", str(config))

    with pytest.raises(VidError) as failure:
        provider_profile("openai", "default", default_env_var="OPENAI_API_KEY")

    message = str(failure.value)
    assert "sk-super-secret-value" not in message
    assert "api_key_env" in message


def test_a_named_profile_resolves_its_own_fields(monkeypatch, tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[providers.openai.work]\napi_key_env = "WORK_OPENAI_KEY"\nbase_url = "https://gateway.example.com/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("VID_CONFIG", str(config))

    section = provider_profile("openai", "work", default_env_var="OPENAI_API_KEY")

    assert section["api_key_env"] == "WORK_OPENAI_KEY"
    assert section["base_url"] == "https://gateway.example.com/v1"


# ---------------------------------------------------------------------------
# 3. No auto-fallback: piper unavailable + an OpenAI key present + no prefix
#    must still refuse. Faked at the boundary this repo owns (`vid.voice`).
# ---------------------------------------------------------------------------


def test_no_auto_fallback_even_with_an_openai_key_present(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("vid.voice.available", lambda: False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-present-but-must-never-be-used")
    monkeypatch.setenv("VID_CONFIG", str(tmp_path / "does-not-exist.toml"))

    with pytest.raises(VidError) as failure:
        speech_preflight(None)

    message = str(failure.value)
    assert "vid[voice]" in message
    assert "openai:" in message
    assert message == NO_SPEECH_BACKEND_HINT


# ---------------------------------------------------------------------------
# 4. The named-env-var-unset refusal: names the profile and the variable,
#    never the key. Needs the `openai` package to get past the prefix gate.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not OPENAI_INSTALLED, reason="openai package not installed in this venv")
def test_named_env_var_unset_names_the_profile_and_variable_no_key_material(monkeypatch, tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[providers.openai.work]\napi_key_env = "WORK_OPENAI_KEY"\n', encoding="utf-8")
    monkeypatch.setenv("VID_CONFIG", str(config))
    monkeypatch.delenv("WORK_OPENAI_KEY", raising=False)

    with pytest.raises(VidError) as failure:
        speech_preflight("openai:alloy@work")

    message = str(failure.value)
    assert "work" in message
    assert "WORK_OPENAI_KEY" in message


# ---------------------------------------------------------------------------
# 5. OpenAI error mapping: fake only the TRANSPORT, raise REAL openai.*Error
#    classes imported from the real package. Skips outright with no `openai`.
# ---------------------------------------------------------------------------


def _fake_client_raising(error: Exception):
    """A client-shaped stand-in whose `.audio.speech.create` raises `error`.

    Fakes the TRANSPORT only -- the attribute shape `openai.OpenAI()`
    exposes -- never the SDK's own exception classes, which are imported and
    raised for real by the tests below.
    """
    import types

    def create(**kwargs):
        raise error

    return types.SimpleNamespace(audio=types.SimpleNamespace(speech=types.SimpleNamespace(create=create)))


def _make_openai_backend(monkeypatch, tmp_path):
    from vid.speech.openai_tts import OpenAIBackend

    monkeypatch.setenv("VID_CONFIG", str(tmp_path / "does-not-exist.toml"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy-never-sent-over-the-wire")
    return OpenAIBackend("alloy", "default")


def _openai_error_builders(openai, httpx2, request):
    return {
        "authentication": lambda: openai.AuthenticationError(
            "bad key", response=httpx2.Response(401, request=request), body=None
        ),
        "rate_limit": lambda: openai.RateLimitError(
            "too many requests", response=httpx2.Response(429, request=request), body=None
        ),
        "timeout": lambda: openai.APITimeoutError(request=request),
        "connection": lambda: openai.APIConnectionError(request=request),
        "status": lambda: openai.APIStatusError(
            "server error", response=httpx2.Response(500, request=request), body=None
        ),
    }


@pytest.mark.parametrize("kind", ["authentication", "rate_limit", "timeout", "connection", "status"])
def test_openai_backend_maps_every_real_sdk_error_to_a_vid_error_with_a_remedy(monkeypatch, tmp_path, kind) -> None:
    openai = pytest.importorskip("openai")
    httpx2 = pytest.importorskip("httpx2")

    request = httpx2.Request("POST", "https://api.openai.com/v1/audio/speech")
    error = _openai_error_builders(openai, httpx2, request)[kind]()

    backend = _make_openai_backend(monkeypatch, tmp_path)
    monkeypatch.setattr(backend, "_client", lambda: _fake_client_raising(error))

    with pytest.raises(VidError) as failure:
        backend.say("hello", tmp_path / f"{kind}.wav")

    message = str(failure.value)
    assert message
    assert "sk-test-dummy" not in message
    assert not isinstance(failure.value, type(error)), "a raw SDK exception must never escape as-is"


def test_openai_backend_error_remedies_are_distinct_per_kind(monkeypatch, tmp_path) -> None:
    openai = pytest.importorskip("openai")
    httpx2 = pytest.importorskip("httpx2")

    request = httpx2.Request("POST", "https://api.openai.com/v1/audio/speech")
    builders = _openai_error_builders(openai, httpx2, request)

    messages = set()
    for kind, build in builders.items():
        backend = _make_openai_backend(monkeypatch, tmp_path)
        monkeypatch.setattr(backend, "_client", lambda b=build: _fake_client_raising(b()))
        with pytest.raises(VidError) as failure:
            backend.say("hello", tmp_path / f"{kind}.wav")
        messages.add(str(failure.value))

    assert len(messages) == len(builders), "every error kind must produce its own, distinguishable remedy"


@pytest.mark.skipif(not OPENAI_INSTALLED, reason="openai package not installed in this venv")
def test_openai_backend_clamps_an_out_of_range_rate_rather_than_erroring(monkeypatch, tmp_path) -> None:
    import types

    from vid.speech.openai_tts import OPENAI_SPEED_RANGE

    backend = _make_openai_backend(monkeypatch, tmp_path)
    seen_speed = {}

    def create(**kwargs):
        seen_speed["value"] = kwargs["speed"]
        raise RuntimeError("stop before any real network call")

    recording_client = types.SimpleNamespace(audio=types.SimpleNamespace(speech=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(backend, "_client", lambda: recording_client)

    with pytest.raises(RuntimeError, match="stop before any real network call"):
        backend.say("hello", tmp_path / "out.wav", rate=999.0)

    assert seen_speed["value"] == OPENAI_SPEED_RANGE[1]


# ---------------------------------------------------------------------------
# 7 & 8. Replay harness against a committed, recorded fixture -- and a
# staleness check that is inert until one exists.
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "openai_tts"
FIXTURE_WAV = FIXTURE_DIR / "sample.wav"
FIXTURE_PROVENANCE = FIXTURE_DIR / "sample.json"
FIXTURE_MAX_AGE_DAYS = 90


def _require_fixture() -> None:
    if not (FIXTURE_WAV.is_file() and FIXTURE_PROVENANCE.is_file()):
        pytest.skip(
            "No recorded OpenAI TTS fixture yet. Record one (needs a real OPENAI_API_KEY) with: "
            "uv run scripts/record_openai_fixture.py -- then commit "
            f"{FIXTURE_WAV.relative_to(FIXTURE_DIR.parents[1])} and "
            f"{FIXTURE_PROVENANCE.relative_to(FIXTURE_DIR.parents[1])}."
        )


def test_openai_replay_against_a_recorded_fixture() -> None:
    """Replays a REAL, committed OpenAI TTS response through this module's
    own duration measurement, and checks the NUMBER -- not merely that one
    was produced.

    `assert duration > 0` is not a measurement: the bug this guards against
    returned 89478.485 s for this very file and would have sailed through it.
    """
    _require_fixture()

    from vid.speech.openai_tts import _duration_of

    provenance = json.loads(FIXTURE_PROVENANCE.read_text(encoding="utf-8"))
    assert provenance["model"]
    assert provenance["voice"]

    expected = _ffprobe_duration_of(FIXTURE_WAV)
    measured = _duration_of(FIXTURE_WAV)
    assert measured == pytest.approx(expected, abs=0.01), (
        f"measured {measured:.3f}s against ffprobe's {expected:.3f}s for the recorded response"
    )


def test_openai_wav_header_is_a_streaming_sentinel_and_stdlib_wave_is_wrong() -> None:
    """The trap, pinned so nobody 'simplifies' the measurement back to `wave`.

    OpenAI's `response_format="wav"` is STREAMED: its RIFF and `data` chunk
    sizes are both 0xFFFFFFFF, because the length is unknown when the header
    is written. Stdlib `wave` does not reject that -- it reports 2147483647
    frames, i.e. ~24.8 hours for a ~3.7 second file. MEASURED 2026-09-23
    against tts-1, tts-1-hd and gpt-4o-mini-tts; all three do this.

    It matters because `narrate.fit()` budgets lines against this number.
    """
    _require_fixture()

    import struct
    import wave

    from vid.speech.openai_tts import _duration_of

    raw = FIXTURE_WAV.read_bytes()
    assert struct.unpack("<I", raw[4:8])[0] == 0xFFFFFFFF, "RIFF size is no longer the streaming sentinel"

    with wave.open(str(FIXTURE_WAV)) as handle:
        naive = handle.getnframes() / handle.getframerate()

    truth = _ffprobe_duration_of(FIXTURE_WAV)
    assert naive > truth * 100, "stdlib `wave` no longer over-reports; re-check whether the fix is still needed"
    assert _duration_of(FIXTURE_WAV) == pytest.approx(truth, abs=0.01)


def _ffprobe_duration_of(path: Path) -> float:
    """Duration straight from ffprobe -- the independent authority this
    module's own measurement is checked against."""
    import shutil
    import subprocess

    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe not on PATH; cannot independently verify the duration")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return float(result.stdout.strip())


def test_openai_replay_fixture_staleness() -> None:
    """FAILS when a present fixture has aged past `FIXTURE_MAX_AGE_DAYS`.
    Inert (skips, never fails) when no fixture has been recorded yet.
    """
    if not FIXTURE_PROVENANCE.is_file():
        pytest.skip("no recorded fixture yet -- staleness is inert until one exists")

    provenance = json.loads(FIXTURE_PROVENANCE.read_text(encoding="utf-8"))
    recorded_at = datetime.fromisoformat(provenance["recorded_at"])
    age = datetime.now(UTC) - recorded_at

    assert age <= timedelta(days=FIXTURE_MAX_AGE_DAYS), (
        f"the recorded OpenAI TTS fixture is {age.days} days old (limit {FIXTURE_MAX_AGE_DAYS}). "
        "Re-record it with `uv run scripts/record_openai_fixture.py`."
    )
