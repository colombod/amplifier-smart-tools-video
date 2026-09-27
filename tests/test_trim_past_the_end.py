"""A trim that starts past the end of the material must say so, not succeed.

Rendered against the 6.0s base fixture, before the guard, `vid` produced a
261-byte MP4 containing NO STREAMS AT ALL and exited 0:

    Trim(start=5.9)   rc=0   2942 bytes   nb_streams=2   duration=0.100000
    Trim(start=6.0)   rc=0    261 bytes   nb_streams=0   duration=None
    Trim(start=1e9)   rc=0    261 bytes   nb_streams=0   duration=None

The reviewer who found it reached it with 1e9 and then correctly qualified the
finding: the magnitude is not what causes it. Being past the end is, and
`start=10` on a ten-second clip does it just as well -- so this is guarded as
the generic case, not as an absurd-value case.

WHY THE COMPILER AND NOT THE MODEL. "Past the end of THIS file" is not a
property of the number, so no bound on `Trim.start` could express it. The
compiler is where the source's duration is known. That is also why
`("Trim", "start")` legitimately remains in the ratchet's ONE_SIDED table: the
model's acceptance is correct, and the render check there accepts a named
refusal as the justifying outcome.
"""

from __future__ import annotations

import pytest

from vid.compile import compile_plan
from vid.plan import Plan, Trim
from vid.schemas import VidError

SOURCE = "talk.mp4"
DURATIONS = {SOURCE: 6.0}


def _compile(start: float, durations: dict[str, float] | None = None) -> list[str]:
    plan = Plan(source=SOURCE).with_operation(Trim(start=start))
    return compile_plan(plan, "out.mp4", durations=DURATIONS if durations is None else durations)


@pytest.mark.parametrize("start", [6.0, 6.1, 10.0, 1e9])
def test_a_trim_starting_past_the_end_is_refused_by_name(start: float) -> None:
    with pytest.raises(VidError) as refusal:
        _compile(start)

    message = str(refusal.value)
    assert f"{start:g}" in message, f"the refusal must name the start it rejected: {message!r}"
    assert "6s" in message, f"the refusal must say how long the material actually is: {message!r}"


@pytest.mark.parametrize("start", [0.0, 1.0, 5.9])
def test_a_trim_inside_the_material_still_compiles(start: float) -> None:
    """The guard must not have taken the working cases with it.

    5.9 on a 6.0s source keeps a tenth of a second -- tiny, but real footage,
    and measured to render two streams. The boundary is `>=`, not `>`.
    """
    assert f"trim=start={start}" in " ".join(_compile(start))


def test_an_unknown_duration_does_not_refuse_everything() -> None:
    """`elapsed` is 0.0 when the length is genuinely unknown.

    That is falsy and must SKIP the check rather than refuse every trim -- a
    guard that fired on "I don't know" would break every plan compiled without
    probing, which is most of the test suite and every `--print-command` run.
    """
    assert "trim=start=1000000000.0" in " ".join(_compile(1e9, durations={}))
