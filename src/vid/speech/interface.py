"""The contract narration speaks through, so the backend stays swappable.

Mirrors `vid.intelligence.interface`: the library depends on this `Protocol`,
never on an SDK directly. Deliberately TWO separate functions here --
`speech_preflight` then `resolve_speech` -- rather than intelligence's single
construct-then-preflight shape. Piper's model load costs about 1.35s; keeping
the cheap check (no load) separate from construction (the load itself) means
a missing prerequisite is caught before that cost is paid, exactly as
`narrate()` already behaves today.

NO AUTO-FALLBACK. OpenAI is resolved only when a caller writes the
`openai:` prefix explicitly. Piper being unavailable is never, by itself, a
reason to spend money on a remote API -- see `speech_preflight`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from vid.schemas import VidError
from vid.speech.openai_tts import DEFAULT_OPENAI_VOICE
from vid.voice import DEFAULT_VOICE


class SpeechBackend(Protocol):
    """Speaks text to a wav file; the library depends on this, never an SDK."""

    implementation: str

    def say(self, text: str, out: Path | str, *, rate: float = 1.0) -> float:
        """Speak `text` into a wav file at `out`. Returns the MEASURED duration.

        `rate` is a caller-facing multiplier -- 1.2 means 20% faster -- never
        a backend-native unit. Piper's own `length_scale` is the inverse of
        this and OpenAI's `speed` is the same direction but a different
        range; translating between them is each backend's own job, never the
        caller's.
        """
        ...


#: Refusal when piper is unavailable and no `openai:` prefix was given.
#: Keeps the literal substring `vid[voice]` that other tests already match on
#: (`tests/test_lib.py::test_narrate_checks_piper_before_the_first_model_call`).
NO_SPEECH_BACKEND_HINT = (
    "No speech synthesiser installed, and no --voice openai:<voice> was given. Narration "
    "needs one:\n"
    "  uv tool install --force 'vid[voice] @ git+https://github.com/colombod/amplifier-smart-tools-video'\n"
    "or opt into the paid remote backend explicitly:\n"
    "  vid narrate ... --voice openai:alloy\n"
    "Every other verb keeps working without either. `vid[voice]` is never installed "
    "automatically, and OpenAI is never used unless you name it -- piper being unavailable "
    "is never a reason to spend money silently."
)


def parse_voice_spec(voice: str | None) -> tuple[str, str, str | None]:
    """A `--voice` value, parsed to `(backend_id, backend_voice, profile)`.

    The grammar is exhaustive:

    | input                  | result                                        |
    |-------------------------|-----------------------------------------------|
    | `None` / `""`           | `("piper", DEFAULT_VOICE, None)`               |
    | `"some-piper-name"`     | `("piper", "some-piper-name", None)`           |
    | `"openai:alloy"`        | `("openai", "alloy", "default")`               |
    | `"openai:alloy@work"`   | `("openai", "alloy", "work")`                  |
    | `"openai:"`             | `("openai", DEFAULT_OPENAI_VOICE, "default")`  |
    | `"openai:@work"`        | `("openai", DEFAULT_OPENAI_VOICE, "work")`     |
    | `"anything-else:..."`   | raises `VidError` naming both valid forms      |

    Exact-match `"openai:"` only -- no case-folding, so `"OpenAI:alloy"` is an
    unrecognised prefix, not a typo this function corrects for you.
    """
    if not voice:
        return ("piper", DEFAULT_VOICE, None)

    prefix, sep, rest = voice.partition(":")
    if not sep:
        return ("piper", voice, None)
    if prefix != "openai":
        raise VidError(
            f"{voice!r} is not a recognised voice. Write either a piper voice name with no "
            f"colon (e.g. {DEFAULT_VOICE!r}) or 'openai:<voice>[@profile]' (e.g. 'openai:alloy' "
            "or 'openai:alloy@work')."
        )

    backend_voice, at_sep, profile = rest.partition("@")
    resolved_voice = backend_voice or DEFAULT_OPENAI_VOICE
    resolved_profile = profile if at_sep else "default"
    return ("openai", resolved_voice, resolved_profile)


def speech_preflight(voice: str | None) -> None:
    """Cheap check that `voice` COULD work: no network call, no backend
    constructed, no piper model load. Raises `VidError` when it cannot.
    """
    backend_id, backend_voice, profile = parse_voice_spec(voice)

    if backend_id == "piper":
        from vid.speech.piper import available as piper_available

        if not piper_available():
            raise VidError(NO_SPEECH_BACKEND_HINT)
        return

    import importlib.util
    import os

    if importlib.util.find_spec("openai") is None:
        raise VidError(
            "The 'openai' package is not installed, and --voice 'openai:...' was requested. "
            "uv tool install --force 'vid[voice-openai] @ "
            "git+https://github.com/colombod/amplifier-smart-tools-video'"
        )

    from vid.config import provider_profile
    from vid.speech.openai_tts import DEFAULT_API_KEY_ENV

    assert profile is not None  # the openai branch of parse_voice_spec always sets one
    section = provider_profile("openai", profile, default_env_var=DEFAULT_API_KEY_ENV)
    env_var = section["api_key_env"]
    if not os.environ.get(env_var):
        # ONE refusal, composed once. Both this preflight and
        # `OpenAIBackend.__init__` check for the key, and writing the sentence
        # twice is how one of them came to omit the manifest's provisioning
        # reference while the other carried it.
        from vid.speech.openai_tts import missing_api_key_error

        raise missing_api_key_error(backend_voice, profile, env_var)


def resolve_speech(voice: str | None) -> SpeechBackend:
    """Construct the backend `voice` selects.

    Costs whatever that backend's construction costs -- piper's ~1.35s model
    load, for the default case -- which is exactly why `speech_preflight` is
    a separate, cheap step meant to run first.
    """
    backend_id, backend_voice, profile = parse_voice_spec(voice)

    if backend_id == "piper":
        from vid.speech.piper import PiperBackend

        return PiperBackend(backend_voice)

    from vid.speech.openai_tts import OpenAIBackend

    assert profile is not None  # the openai branch of parse_voice_spec always sets one
    return OpenAIBackend(backend_voice, profile)
