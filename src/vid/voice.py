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
from pathlib import Path
import subprocess
import sys
import wave

from vid.core.writes import writing
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

    with writing(
        directory,
        "The voice model directory",
        "That location comes from VID_VOICES_DIR when it is set. Point it at a writable directory.",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "piper.download_voices", name, "--download-dir", str(directory)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not model.is_file():
        detail = (result.stderr or "").strip().splitlines()
        raise VidError(
            f"Could not download the voice {name!r}: "
            f"{detail[-1] if detail else 'no reason given'}\n"
            "Voices are fetched anonymously from a public repository; this is usually a network "
            "problem. Check connectivity and retry."
        )
    return model


class Speaker:
    """A loaded voice. Loading costs about a second, so it is done once."""

    def __init__(self, name: str = DEFAULT_VOICE) -> None:
        if not available():
            raise VidError(INSTALL_HINT)
        # `available()` proves the piper PACKAGE imports. This name is a
        # separate import that a partial or version-skewed install can still
        # fail, and `main()` catches only VidError -- so it is guarded rather
        # than trusted, like every other third-party name in this file.
        try:
            from piper import PiperVoice
        except Exception as exc:
            raise VidError(f"{INSTALL_HINT}\n(piper is present but would not load: {exc})") from exc

        model = ensure_voice(name)
        # `available()` above proves piper is IMPORTABLE. It does not prove the
        # voice model loads: a truncated download, an onnxruntime built for
        # another architecture, or a model from an incompatible piper all fail
        # HERE. `main()` catches only VidError, so every one of those reached
        # the user as a Python traceback.
        try:
            with _quiet_stderr():
                self._voice = PiperVoice.load(str(model))
        except Exception as exc:
            raise VidError(
                f"The voice {name!r} is installed but would not load: {exc}\n"
                f"Delete {model} and let it download again, or pick another voice with "
                "`--voice`. `vid check` reports whether the speech backend is working."
            ) from exc
        self.name = name

    def say(self, text: str, out: Path | str, *, rate: float = 1.0) -> float:
        """Speak `text` into a wav file. Returns its measured duration.

        MEASURED, not estimated. Word counts and syllable heuristics are how a
        narration overruns its slot by twenty percent and nobody notices until
        the render. The file exists; its length is a fact.
        """
        out = Path(out)
        with writing(out, "The synthesised speech", "Choose a writable --out path, or free space on this one."):
            out.parent.mkdir(parents=True, exist_ok=True)

        # piper expresses rate as a length scale: >1 is SLOWER. A caller
        # asking for 1.2x speed wants a scale of 1/1.2. Passed as an explicit
        # keyword rather than a `**kwargs` dict: a dict of one value type is
        # still a value type that could apply to ANY of synthesize_wav's other
        # keyword parameters, which is exactly what made this unpack-checkable
        # as `bool` in one place and `SynthesisConfig` in another.
        # SIBLING OF THE LOAD WRAP ABOVE. Synthesis reaches the same native
        # stack, so an onnxruntime fault or an unwritable output path surfaced
        # here as a traceback too. Found by sweeping for bare third-party
        # calls rather than fixing only the line a review cited.
        try:
            from piper import SynthesisConfig

            syn_config = SynthesisConfig(length_scale=1.0 / rate) if rate != 1.0 else None
            with _quiet_stderr(), wave.open(str(out), "wb") as handle:
                self._voice.synthesize_wav(text, handle, syn_config=syn_config)
        except VidError:
            raise
        except Exception as exc:
            raise VidError(
                f"The voice {self.name!r} could not speak that line: {exc}\n"
                f"Check {out.parent} is writable, or run `vid check` to confirm the speech "
                "backend is working."
            ) from exc
        return duration_of(out)


def duration_of(path: Path | str) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())
