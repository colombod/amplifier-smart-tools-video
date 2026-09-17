"""Timecodes, parsed the way a person writes them.

A caller writes `1:05`, `0:10.5`, `90`, or `1:02:03`. Every one of those is a
position in seconds, and the tool should accept all of them rather than making
somebody count. Seconds are the internal unit; the string forms exist only at
the edge.
"""

from __future__ import annotations

import re

from vid.schemas import VidError

_NUMERIC = re.compile(r"^\d+(\.\d+)?$")


def parse_timecode(text: str | float | int) -> float:
    """Seconds from `90`, `1:30`, `1:02:03`, or `0:10.5`.

    Fails loudly on anything else. A timecode that silently becomes 0.0 is how a
    caller ends up with a clip that starts at the beginning and never learns why.
    """
    if isinstance(text, int | float):
        value = float(text)
        if value < 0:
            raise VidError(f"A timecode cannot be negative; got {value}.")
        return value

    raw = str(text).strip()
    if not raw:
        raise VidError("A timecode cannot be empty. Write seconds (`90`), `m:ss`, or `h:mm:ss`.")

    parts = raw.split(":")
    if len(parts) > 3:
        raise VidError(f"{raw!r} has too many colons. Write seconds, `m:ss`, or `h:mm:ss`.")

    for part in parts:
        if not _NUMERIC.match(part):
            raise VidError(
                f"{raw!r} is not a timecode. Write seconds (`90`), `m:ss` (`1:30`), "
                f"or `h:mm:ss` (`1:02:03`); the last part may carry decimals."
            )

    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def format_timecode(seconds: float) -> str:
    """The inverse, for anything a person reads back."""
    if seconds < 0:
        raise VidError(f"A timecode cannot be negative; got {seconds}.")
    whole = int(seconds)
    frac = seconds - whole
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    tail = f"{secs:02d}" if not frac else f"{secs + frac:06.3f}".rstrip("0").rstrip(".")
    if hours:
        return f"{hours}:{minutes:02d}:{tail}"
    return f"{minutes}:{tail}"


def parse_speed(text: str) -> float:
    """`2x`, `0.5x`, or a bare number. Rejects zero and negatives with a reason."""
    raw = str(text).strip().lower().removesuffix("x")
    try:
        speed = float(raw)
    except ValueError as exc:
        raise VidError(f"{text!r} is not a speed. Write `2x`, `0.5x`, or a bare number.") from exc
    if speed <= 0:
        raise VidError(
            f"A speed must be greater than zero; got {speed}. "
            "To play a clip backwards, that is a different operation and this tool does not have it yet."
        )
    return speed
