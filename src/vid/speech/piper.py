"""Piper, wrapped behind the `SpeechBackend` protocol.

`vid.voice` is not touched by this module -- it is the thing being wrapped.
Every call here delegates to it, imported locally (not at module scope) so
this module keeps behaving correctly under the existing test suite's pattern
of monkeypatching `vid.voice.available` / `vid.voice.Speaker` at call time.
"""

from __future__ import annotations

from pathlib import Path

from vid.schemas import VidError


def available() -> bool:
    """Whether piper is installed. Delegates to `vid.voice.available`."""
    from vid.voice import available as voice_available

    return voice_available()


class PiperBackend:
    """Speaks locally through `vid.voice.Speaker`. The default backend."""

    implementation = "piper"

    def __init__(self, name: str) -> None:
        from vid.voice import Speaker

        self._speaker = Speaker(name)
        self.name = name

    def say(self, text: str, out: Path | str, *, rate: float = 1.0) -> float:
        """Speak `text` into a wav file. Returns the MEASURED duration.

        Any failure out of piper's own synthesis is wrapped in a `VidError`
        with a remedy -- today it propagates whatever piper/onnxruntime
        raised, with nothing a caller could act on.
        """
        try:
            return self._speaker.say(text, out, rate=rate)
        except VidError:
            raise
        except Exception as error:
            raise VidError(
                f"Piper failed to synthesise speech: {error}\n"
                "Run `vid check` to confirm the speech synthesiser is working, or try a "
                "different --voice."
            ) from error
