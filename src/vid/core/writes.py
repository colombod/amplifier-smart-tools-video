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
from contextlib import contextmanager
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
