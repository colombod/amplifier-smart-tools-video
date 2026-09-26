"""OpenAI's cloud TTS: an explicit ALTERNATIVE to piper, never a fallback.

Only ever constructed when a caller writes `--voice openai:<voice>[@profile]`
by name -- piper being unavailable is never a reason to reach for this on its
own, because it costs money and sends the narration TEXT to a remote API.

The `openai` package is imported lazily, inside functions, everywhere in this
module. Module-level constants must stay importable with zero extras
installed (`vid.speech.interface` imports `DEFAULT_OPENAI_VOICE` from here
unconditionally), and CI installs this package with no extras at all.
"""

from __future__ import annotations

import os
from pathlib import Path

from vid.core.writes import writing
from vid.probe import require_ffprobe
from vid.schemas import VidError

DEFAULT_OPENAI_VOICE = "alloy"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini-tts"

#: Which environment variable the implicit "default" profile reads when no
#: config file exists at all. See `vid.config.provider_profile`.
DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"

#: Verified against OpenAI's createSpeech docs: `speed` accepts 0.25-4.0.
OPENAI_SPEED_RANGE = (0.25, 4.0)


def available(profile: str = "default") -> bool:
    """Whether the `openai` package is importable AND the profile's named
    env var is currently set. NO network call.

    Best-effort and never raises: any config problem (missing profile,
    malformed file) is reported here as simply "not available". The detailed
    reason -- naming the profile, the file, or the missing variable -- comes
    from `vid.speech.interface.speech_preflight`, which calls the same
    configuration lookup directly rather than through this function.
    """
    import importlib.util

    if importlib.util.find_spec("openai") is None:
        return False

    from vid.config import provider_profile

    try:
        section = provider_profile("openai", profile, default_env_var=DEFAULT_API_KEY_ENV)
    except VidError:
        return False
    return bool(os.environ.get(section["api_key_env"]))


_SPEECH_REMEDY = (
    "Choose a writable --out path, or free space on this one. The synthesis itself "
    "succeeded, so retrying costs another API call."
)


def missing_api_key_error(voice: str, profile: str, env_var: str) -> VidError:
    """The ONE missing-key refusal, for the two places that check.

    `speech_preflight` checks before any synthesis starts and
    `OpenAIBackend.__init__` checks again when the backend is built, so the
    same sentence is needed twice. It was WRITTEN twice, and when the first
    was corrected to carry the manifest's provisioning reference the second
    kept telling the caller to export a key without saying where one comes
    from -- reported as a fresh deviation one run later.

    Same reasoning as `probe.require_ffmpeg_tools`: a sentence composed in two
    places is a sentence that will be right in one of them.
    """
    from vid.core.manifest import manifest_install

    return VidError(
        f"--voice 'openai:{voice}@{profile}' needs {env_var} set (profile "
        f"{profile!r} reads it), and it is not. Export it ({manifest_install('openai-api-key')}), "
        f"or point profile {profile!r} at a different api_key_env in your vid config."
    )


class OpenAIBackend:
    """Sends narration text to OpenAI's TTS API for the named voice/profile."""

    implementation = "openai-tts"

    def __init__(self, voice: str, profile: str) -> None:
        from vid.config import provider_profile

        section = provider_profile("openai", profile, default_env_var=DEFAULT_API_KEY_ENV)
        self._env_var = section["api_key_env"]
        self._model = section.get("model", DEFAULT_OPENAI_MODEL)
        self._base_url = section.get("base_url")
        self._profile = profile
        self.voice = voice

        api_key = os.environ.get(self._env_var)
        if not api_key:
            raise missing_api_key_error(voice, profile, self._env_var)
        self._api_key = api_key

    def _client(self):
        # THE OPENAI SIBLING of the copilot constructor guard. Both build a
        # provider client outside any conversion, and `cli.main` translates
        # only VidError -- so a bad `base_url` in a profile, or a broken SDK
        # install, reached the caller as a traceback naming neither. The
        # copilot one was fixed when it was reported; this one was its
        # unreported twin, found one run later.
        try:
            import openai

            # Two explicit calls rather than **kwargs off a plain dict -- a dict
            # of one value type loses each keyword's own, more specific type,
            # which is exactly what made a `**kwargs` unpack unreadable to `ty`
            # here (`base_url` and `api_key` are both `str`, but `OpenAI.__init__`
            # accepts many other keyword shapes on that same overload).
            if self._base_url:
                return openai.OpenAI(api_key=self._api_key, base_url=self._base_url)
            return openai.OpenAI(api_key=self._api_key)

        except VidError:
            # Straight through, for the same reason as the copilot guard: a
            # precise diagnosis must not be replaced by a general one.
            raise
        except Exception as exc:
            raise VidError(
                f"The OpenAI speech client could not be created for profile "
                f"{self._profile!r}: {exc}.\n"
                "Check that profile's base_url and api_key_env in your vid config, and that "
                "the 'openai' package is installed -- `vid check` reports what this "
                "installation can actually do."
            ) from exc

    def say(self, text: str, out: Path | str, *, rate: float = 1.0) -> float:
        """Speak `text` via OpenAI's TTS API. Returns the MEASURED duration
        of the written wav.

        `rate` is CLAMPED into OpenAI's documented `speed` range
        (0.25-4.0) rather than left to fail on the far side of a network
        call -- narrate's own MAX_RATE (1.25) sits comfortably inside it, so
        this only ever matters for an out-of-range caller-supplied value,
        and the clamp is documented here rather than silent.
        """
        import openai

        lo, hi = OPENAI_SPEED_RANGE
        speed = min(max(rate, lo), hi)

        out = Path(out)
        # BEFORE THE API CALL, deliberately: discovering an unwritable
        # destination after the request would waste a paid synthesis, the
        # same way the narration copy did.
        with writing(out, "The synthesised speech", _SPEECH_REMEDY):
            out.parent.mkdir(parents=True, exist_ok=True)
        client = self._client()
        try:
            response = client.audio.speech.create(
                model=self._model,
                voice=self.voice,
                input=text,
                response_format="wav",
                speed=speed,
            )
        except openai.AuthenticationError as error:
            raise VidError(
                f"OpenAI rejected the API key named by {self._env_var} (profile "
                f"{self._profile!r}). Check the key is current and has TTS access."
            ) from error
        except openai.RateLimitError as error:
            raise VidError(
                "OpenAI's TTS rate limit was hit. Wait and retry, or drop the 'openai:' "
                "prefix to speak locally with piper instead."
            ) from error
        except openai.APITimeoutError as error:
            raise VidError(
                "The request to OpenAI's TTS API timed out. Retry, or check network connectivity."
            ) from error
        except openai.APIConnectionError as error:
            raise VidError(
                "Could not reach OpenAI's TTS API. Check network connectivity, or drop the "
                "'openai:' prefix to speak locally with piper instead."
            ) from error
        except openai.APIStatusError as error:
            raise VidError(
                f"OpenAI's TTS API returned an error (HTTP {error.status_code}). Retry, or "
                "check https://status.openai.com/."
            ) from error

        # The API call SUCCEEDED and was billed; a filesystem failure here
        # must not come back as a traceback that looks like the API broke.
        with writing(out, "The synthesised speech", _SPEECH_REMEDY):
            out.write_bytes(response.content)
        return _duration_of(out)


def _duration_of(path: Path) -> float:
    """The written wav's measured duration, taken from the audio ACTUALLY PRESENT.

    Do NOT measure this with stdlib `wave` (which is what `vid.voice.duration_of`
    correctly uses for piper). OpenAI returns a STREAMING wav whose RIFF and
    `data` chunk sizes are both the 0xFFFFFFFF sentinel, because the length is
    not known when the header is written. `wave` does not reject that -- it
    divides the sentinel by the block align and reports 2147483647 frames, so a
    3.66-second file measures as 89478.485 seconds.

    MEASURED against a real `tts-1` response on 2026-09-23: 175,844 bytes,
    24000 Hz mono 16-bit, `data` size declared 4294967295, true duration
    3.6625 s (ffprobe agrees to the millisecond). That is a 24,400x error, and
    it arrives as a plausible float rather than an exception -- which is
    precisely why a try/except around `wave` cannot catch it.

    It matters: `narrate.fit()` compares this number against each slot's budget.
    A duration of 89478 s makes EVERY line overrun, exhausting the rewrite and
    rate-nudge attempts and reporting every line as unfitted -- from an API call
    that succeeded.

    So: parse the chunks, clamp the declared `data` size to the bytes genuinely
    on disk, and divide by the frame rate. For a well-formed wav the declared
    size and the real size agree and this is the same answer `wave` would give.
    ffprobe remains the fallback for anything this cannot parse as PCM.
    """
    measured = _duration_from_data_bytes(path)
    if measured is not None:
        return measured
    return _ffprobe_duration(path)


def _duration_from_data_bytes(path: Path) -> float | None:
    """Duration derived from the `data` bytes actually on disk, or None.

    Returns None -- deferring to ffprobe -- for anything that is not a plain
    PCM RIFF/WAVE file, rather than guessing.
    """
    import struct

    raw = path.read_bytes()
    if len(raw) < 12 or raw[0:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None

    frame_rate = 0
    block_align = 0
    offset = 12
    while offset + 8 <= len(raw):
        chunk_id = raw[offset : offset + 4]
        declared = struct.unpack("<I", raw[offset + 4 : offset + 8])[0]
        body = offset + 8
        if chunk_id == b"fmt " and body + 16 <= len(raw):
            _, channels, frame_rate, _, block_align, bits = struct.unpack("<HHIIHH", raw[body : body + 16])
            if not block_align:
                block_align = max(1, channels * (bits // 8))
        elif chunk_id == b"data":
            # The sentinel case: never trust `declared` past the end of the file.
            present = min(declared, len(raw) - body)
            if not frame_rate or not block_align:
                return None
            return present / block_align / frame_rate
        offset = body + declared + (declared % 2)
        if declared > len(raw):  # a sentinel size would otherwise overflow the walk
            return None
    return None


def _ffprobe_duration(path: Path) -> float:
    import subprocess

    # Reached only when `_duration_from_data_bytes` could not parse the
    # response as PCM -- rare, but a missing binary must still refuse with a
    # named remedy rather than a bare `FileNotFoundError` from `subprocess.run`.
    require_ffprobe(
        f"the OpenAI TTS output at {path} did not parse as plain PCM, so measuring it falls back to ffprobe"
    )
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise VidError(
            f"Could not measure the OpenAI TTS output at {path}: neither stdlib `wave` nor "
            "ffprobe could read it. Run `vid check` to confirm ffprobe is on PATH."
        )
    return float(result.stdout.strip())
