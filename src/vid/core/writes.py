"""One rule for every write this tool makes to disk.

THE CLASS WITH THE MOST MEMBERS FOUND SO FAR. `cli.main` translates only
`VidError` into a named, non-zero exit, so any filesystem call that can raise
`OSError` and is not converted reaches the user as a bare traceback naming
neither what failed nor what to do. Eight such calls shipped.

They were found one or two at a time, over four rounds, each time by someone
else:

    index.save          reported, fixed -- names VID_INDEX_DIR
    index.load          found sweeping its sibling
    index.fingerprint   found by a test written for the sibling
    lib narration       reported by check-spec-adherence run 6
    openai_tts x2       reported by check-spec-adherence run 8
    voice x2            never reported -- found by enumerating this module
    vision, compile,    never reported -- same enumeration
    color, narrate

Four rounds of fixing the REPORTED instance is what made the eleventh one
still be there. The remedy is not another careful fix: it is that every write
goes through one place, so the next one cannot be added unguarded without
saying so out loud.

WHAT THIS DOES NOT DO. It does not retry, it does not clean up a partial
write, and it does not invent a fallback location -- a tool that silently
writes somewhere the caller did not ask for is a worse outcome than a refusal.
It converts the failure into a sentence naming the destination and a remedy,
which is the whole requirement.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from vid.schemas import VidError


@contextmanager
def writing(destination: Path | str, what: str, remedy: str) -> Iterator[None]:
    """Convert any `OSError` from the enclosed write into a named `VidError`.

    `what` names the artefact ("The narration track"), `remedy` is the
    caller's own sentence about what to do -- usually naming the environment
    variable that chose the location, because that is the only knob the caller
    has.

    A context manager rather than a wrapper function because the sites are
    two-or-three-call sequences (`mkdir` then `write`), and the failure is the
    same failure wherever in the sequence it lands.
    """
    try:
        yield
    except OSError as exc:
        raise VidError(f"{what} could not be written to {destination}: {exc.strerror or exc}.\n{remedy}") from exc


@contextmanager
def writing_atomically(destination: Path | str, what: str, remedy: str) -> Iterator[Path]:
    """As `writing`, but the destination NEVER holds a partial artefact.

    FOR A PERSISTENT ARTEFACT SOMETHING LATER TREATS AS VALID. `writing` above
    converts the failure and deliberately leaves the partial file alone, which
    is right for a caller-named output the person can see and delete. It is
    WRONG for a cache, and the LUT cache proved it:

        cube = lut_cache_dir() / f"{key}.cube"
        if not cube.is_file():
            ... generate it ...

    Existence IS the validity check. So a write that died halfway -- a full
    disk, a killed process -- left a truncated `.cube` at exactly the path the
    next run tests for, and every later recolor skipped regeneration and fed
    that truncated table to ffmpeg. The first failure is loud; the poisoned
    cache after it is silent and permanent.

    The fix is structural rather than another message: write to a temporary
    file beside the destination, then `os.replace` it into place. `os.replace`
    is atomic within a filesystem, and the temporary sits in the destination's
    OWN directory so it is always the same filesystem. A reader therefore sees
    either no file or the complete one -- never a half-written one.

    Yields the temporary path to write to. On any failure the temporary is
    removed and the destination is left exactly as it was.
    """
    import os
    import tempfile

    destination = Path(destination)
    temporary: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, raw = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.name}.", suffix=".partial")
        os.close(handle)
        temporary = Path(raw)
        yield temporary
        temporary.replace(destination)
        temporary = None
    except OSError as exc:
        raise VidError(f"{what} could not be written to {destination}: {exc.strerror or exc}.\n{remedy}") from exc
    finally:
        # Covers the non-OSError paths too: an exception raised by the caller's
        # own body must not leave a `.partial` behind either.
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink()
