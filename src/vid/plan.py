"""The edit plan: what travels through the pipe.

Every verb reads a plan on stdin, appends one operation, and writes the plan to
stdout. Nothing decodes a frame until `render`. That is the whole architecture,
and this module is its vocabulary.

A plan is JSON, so every verb that builds one is deterministic, instant, and
needs neither ffmpeg nor a provider. That is not a property we engineer around;
it is what falls out of not touching pixels until the end.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from vid.schemas import VidError

PLAN_FORMAT = 1


class Trim(BaseModel):
    """Keep a time range, discard the rest."""

    op: Literal["trim"] = "trim"
    start: float = 0.0
    end: float | None = None


class Cut(BaseModel):
    """Remove a time range, keeping what surrounds it."""

    op: Literal["cut"] = "cut"
    start: float
    end: float


class RampPoint(BaseModel):
    """One `speed@time` control point on a speed curve."""

    at: float
    speed: float


class Retime(BaseModel):
    """A constant speed change, or a ramp between control points.

    `atempo` takes a scalar, so a ramp cannot be expressed as one filter. It is
    decomposed into constant-speed regions at compile time -- which is the
    constraint that made the whole plan-as-pipe design necessary.
    """

    op: Literal["retime"] = "retime"
    speed: float | None = None
    ramp: list[RampPoint] = Field(default_factory=list)
    pitch: bool = True


class Zoom(BaseModel):
    """Animated zoom (Ken Burns) centred on a moment."""

    op: Literal["zoom"] = "zoom"
    to: float = 1.3
    at: float | None = None
    duration: float = 3.0
    x: str = "iw/2-(iw/zoom/2)"
    y: str = "ih/2-(ih/zoom/2)"


class Stitch(BaseModel):
    """Append other sources. `-` stands for the plan arriving on stdin."""

    op: Literal["stitch"] = "stitch"
    sources: list[str]
    transition: str | None = None
    transition_duration: float = 0.5
    # Where the blend begins, measured from the start of the running edit. xfade
    # needs an absolute offset and cannot compute one, so whoever appends the
    # operation resolves it -- by probing durations, which is why this is on the
    # plan rather than invented at compile time.
    transition_offset: float = 0.0
    # PROVENANCE, and it is what keeps a model out of the render path. When a
    # caller describes a transition instead of naming one, a model resolves the
    # description ONCE, here, and the plan stores both what was asked and what
    # was chosen. Re-rendering never calls a model again: the same plan renders
    # identically on a machine with no credentials, and a person can read the
    # choice before a frame is touched.
    transition_requested: str | None = None
    transition_rationale: str | None = None
    # TIER 3. A generated xfade expression, present only when `transition` is
    # "custom". It reaches a plan ONLY after being rendered as a probe against
    # THESE clips and passing the blend checks -- so a plan never carries an
    # expression whose behaviour is unknown.
    #
    # Verified against the specific clips it will be used on, deliberately. The
    # expression is deterministic, so proving it once for this pair is enough
    # for this pair; reusing it against a different pair would be a claim nobody
    # checked.
    transition_expr: str | None = None
    transition_verified: bool = False


class Caption(BaseModel):
    """Burn subtitles in from a subtitle file."""

    op: Literal["caption"] = "caption"
    subtitles: str
    style: str | None = None


Operation = Annotated[
    Trim | Cut | Retime | Zoom | Stitch | Caption,
    Field(discriminator="op"),
]


class Plan(BaseModel):
    """An edit, described but not performed."""

    plan_format: int = PLAN_FORMAT
    source: str | None = None
    operations: list[Operation] = Field(default_factory=list)

    def with_operation(self, operation: Operation) -> Plan:
        """A new plan with one more step. Plans are never mutated in place."""
        return Plan(
            plan_format=self.plan_format,
            source=self.source,
            operations=[*self.operations, operation],
        )


def read_plan(source: str | None = None) -> Plan:
    """The plan arriving on stdin, or a new one rooted at `source`.

    A verb given a file argument starts a plan. The same verb given nothing
    continues one. Getting neither is a real error with a real remedy, not an
    empty plan that fails confusingly three verbs later.
    """
    if source is not None and source != "-":
        return Plan(source=source)

    if sys.stdin.isatty():
        raise VidError(
            "No input. Either name a video file, or pipe a plan in from another verb -- "
            "for example `vid trim talk.mp4 --from 0:10 | vid zoom --to 1.4`."
        )

    raw = sys.stdin.read().strip()
    if not raw:
        raise VidError(
            "The plan arriving on stdin was empty. The verb upstream produced nothing; "
            "run it alone to see what it said."
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        preview = raw[:120] + ("..." if len(raw) > 120 else "")
        raise VidError(
            f"What arrived on stdin is not a plan -- it is not JSON. It began: {preview!r}. "
            "Every verb except `render` writes a plan to stdout; check what produced this."
        ) from exc

    if isinstance(data, dict) and data.get("plan_format") not in (None, PLAN_FORMAT):
        raise VidError(
            f"That plan is format {data.get('plan_format')}, and this tool speaks format {PLAN_FORMAT}. "
            "Re-create it with this version rather than hand-editing the number."
        )

    return Plan.model_validate(data)


def write_plan(plan: Plan) -> None:
    """The plan, to stdout, for the next verb in the pipe."""
    sys.stdout.write(plan.model_dump_json(indent=2, exclude_none=False) + "\n")
