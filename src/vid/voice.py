"""Text to speech, locally.

`piper-tts`, and not because it sounds best -- because it is the only local
engine an agent can install unattended and expect to succeed. Verified in a
clean container: a 32.6 MiB prebuilt wheel, no compiler, and ZERO version
changes to anything already installed, because piper declares
`onnxruntime<2,>=1` with no numpy pin and parks torch/cython/cmake behind a
`train` extra a plain install never touches.

The voice model is the real cost: 60 MiB, fetched once, anonymously.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import wave
from pathlib import Path

from vid.schemas import VidError

#: The default voice. Medium quality is the honest middle: `low` sounds
#: synthetic enough to distract from what is being said, `high` is several times
#: the download for a difference most listeners will not name.
DEFAULT_VOICE = "en_US-lessac-medium"

INSTALL_HINT = (
    "No speech synthesiser installed. Narration needs one:\n"
    "  uv tool install --force 'vid[voice] @ "
    "git+https://github.com/colombod/amplifier-smart-tools-video'\n"
    "Every other verb keeps working without it -- `vid check` shows what you have."
)


def voices_dir() -> Path:
    """Where voice models are cached. Shared, because a 60 MiB download is not
    something to repeat per project."""
    override = os.environ.get("VID_VOICES_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "vid" / "voices"


def available() -> bool:
    import importlib.util

    return importlib.util.find_spec("piper") is not None


@contextlib.contextmanager
def _quiet_stderr():
    """Swallow onnxruntime's session-init complaint, and NOTHING else.

    Under a restricted cpuset -- Docker with `--cpuset`, most CI, any container
    with a CPU limit -- onnxruntime prints a red `[E:onnxruntime:...]
    pthread_setaffinity_np failed` line every time a session opens. It is
    cosmetic: exit 0, audio correct. But it is an ERROR line, and a caller
    watching stderr reasonably reads it as a failure.

    piper 1.8.0 offers no hook to prevent it -- `PiperVoice.load()` rejects
    `session_options`, and `OMP_NUM_THREADS=1` does not help because onnxruntime
    manages its own pool. So it is filtered at the only place left, and filtered
    NARROWLY: anything that is not that specific known-benign line is passed
    through, because swallowing a real error to hide a cosmetic one would be a
    far worse trade.
    """
    captured = io.StringIO()
    real = sys.stderr
    try:
        sys.stderr = captured
        yield
    finally:
        sys.stderr = real
        for line in captured.getvalue().splitlines():
            if "pthread_setaffinity_np" in line or "Specify the number of threads" in line:
                continue
            if line.strip():
                print(line, file=real)


def ensure_voice(name: str = DEFAULT_VOICE) -> Path:
    """Fetch a voice model if it is not already cached. Returns its path.

    An anonymous HTTPS download from a public repository -- no token, no account.
    Confirmed on a box with no credential store of any kind.
    """
    if not available():
        raise VidError(INSTALL_HINT)

    directory = voices_dir()
    model = directory / f"{name}.onnx"
    if model.is_file():
        return model

    directory.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "piper.download_voices", name, "--download-dir", str(directory)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not model.is_file():
        detail = (result.stderr or "").strip().splitlines()
        raise VidError(
            f"Could not download the voice {name!r}: "
            f"{detail[-1] if detail else 'no reason given'}\n"
            f"Voices are fetched anonymously from a public repository; this is usually a network problem."
        )
    return model


class Speaker:
    """A loaded voice. Loading costs about a second, so it is done once."""

    def __init__(self, name: str = DEFAULT_VOICE) -> None:
        if not available():
            raise VidError(INSTALL_HINT)
        from piper import PiperVoice

        model = ensure_voice(name)
        with _quiet_stderr():
            self._voice = PiperVoice.load(str(model))
        self.name = name

    def say(self, text: str, out: Path | str, *, rate: float = 1.0) -> float:
        """Speak `text` into a wav file. Returns its measured duration.

        MEASURED, not estimated. Word counts and syllable heuristics are how a
        narration overruns its slot by twenty percent and nobody notices until
        the render. The file exists; its length is a fact.
        """
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)

        kwargs = {}
        if rate != 1.0:
            # piper expresses rate as a length scale: >1 is SLOWER. A caller
            # asking for 1.2x speed wants a scale of 1/1.2.
            from piper import SynthesisConfig

            kwargs["syn_config"] = SynthesisConfig(length_scale=1.0 / rate)

        with _quiet_stderr():
            with wave.open(str(out), "wb") as handle:
                self._voice.synthesize_wav(text, handle, **kwargs)
        return duration_of(out)


def duration_of(path: Path | str) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())
