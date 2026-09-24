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
import re
import sys
from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

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


class Mask(BaseModel):
    """What shape an overlay is cut to, and where that shape comes from.

    Polymorphic on `kind` rather than a fixed set of shape flags, so the same
    field covers a procedural cut-out, a piece of supplied art, and a matte
    driven per frame by another clip. A caller who outgrows the built-in shapes
    reaches for `image` or `video` instead of waiting for a new enum member.

    Geometry is described relative to the layer, never in absolute pixels, so a
    mask survives the layer being resized.
    """

    kind: Literal["rect", "rounded_rect", "circle", "ellipse", "image", "video"]
    # The matte file, for `image` and `video`. Ignored by the procedural shapes.
    source: str | None = None
    # Corner radius in pixels, for `rounded_rect` only.
    radius: int = 40
    # Swap keep for drop: the shape becomes a hole rather than a window.
    invert: bool = False
    # Soften the matte's edge, in pixels. 0 is a hard edge.
    feather: float = 0.0


#: A hex literal (`0xRRGGBB`, `#RRGGBBAA`) or a bare colour name, each with an
#: optional `@alpha`. Anything carrying a filtergraph metacharacter is refused.
_COLOUR = re.compile(r"(?:(?:0x|#)[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?|[A-Za-z]+)(?:@[0-9]*\.?[0-9]+)?")


class Key(BaseModel):
    """Make part of the layer's OWN picture transparent, by colour or brightness.

    Distinct from a mask: a mask is a shape imposed from outside, a key is a
    property of what the layer already contains. A caller may key a green
    screen AND cut the result to a circle in the same operation, and the two
    combine by intersection.
    """

    kind: Literal["colorkey", "chromakey", "lumakey"] = "colorkey"
    #: The colour to remove, for `colorkey` and `chromakey`. Ignored by `lumakey`.
    #:
    #: VALIDATED, because this is the ONE caller-supplied free-form string that
    #: reaches ffmpeg's filter text. Everything else the compiler interpolates
    #: is a number or a closed Literal, and mask sources become `-i` inputs
    #: rather than filter arguments. An independent review showed a colour of
    #: `0x00FF00,negate` emitting
    #:     [1:v]colorkey=color=0x00FF00,negate:similarity=...
    #: which inserted a whole extra filter and visibly turned blue to yellow.
    #: A comma, colon, quote or bracket is a filtergraph metacharacter, so the
    #: field is checked against a closed shape instead of being escaped.
    colour: str = "0x00FF00"
    #: The brightness to remove, 0..1, for `lumakey` only.
    threshold: float = 0.9
    #: How close a pixel must be to count. Higher takes more.
    similarity: float = 0.3
    #: Softness at the edge of what was taken.
    blend: float = 0.0

    @field_validator("colour")
    @classmethod
    def _colour_is_a_colour(cls, value: str) -> str:
        """Either a hex literal or a bare colour name, with an optional alpha.

        Deliberately a closed allowlist rather than a blocklist of dangerous
        characters: a blocklist has to anticipate every metacharacter ffmpeg's
        parser treats specially, and being wrong once reopens the hole.
        """
        if not _COLOUR.fullmatch(value):
            raise ValueError(
                f"Unusable colour {value!r}. Use a hex literal like `0x00FF00` or `#00FF00`, "
                "or a colour name like `green`, either optionally followed by `@` and an "
                "alpha such as `green@0.5`."
            )
        return value


class LayerAudio(BaseModel):
    """What happens to the overlay layer's own sound.

    Absent from an overlay means `drop`, and that default is the point: in the
    common picture-in-picture case the base already carries the narration, so a
    layer that quietly added its own would duplicate it. Inclusion is stated.

    The compressor settings are fields rather than constants so a plan records
    what it actually did. The CLI exposes only `--duck`; a caller who needs to
    tune the shape edits the plan, which is the contract, where the CLI is
    ergonomics.
    """

    policy: Literal["drop", "keep", "only"] = "drop"
    #: Applied to the LAYER before mixing, in dB. 0 leaves it alone.
    gain_db: float = 0.0
    #: Applied to the BASE before mixing, in dB.
    base_gain_db: float = 0.0
    #: Dip the base under the layer, recovering after. Off by default.
    duck: bool = False
    duck_threshold: float = 0.05
    duck_ratio: float = 8.0
    duck_attack: float = 20.0
    duck_release: float = 250.0


class Motion(BaseModel):
    """Where the overlay travels to, and over what window.

    The overlay's own `x`/`y`/`width`/`height` are where the motion STARTS;
    these fields are where it ends. One geometry is stated twice rather than a
    list of keyframes, because the requested case is a single move -- an inset
    growing to full screen -- and a keyframe list would be a larger promise than
    anything has asked for.

    This animates PRESENTATION only. The layer is never retimed, so its own
    source timing is untouched by construction.
    """

    to_x: int = 0
    to_y: int = 0
    # None means "stay the size it already was", so a pure move needs no size.
    to_width: int | None = None
    to_height: int | None = None
    start: float = 0.0
    duration: float = 1.0
    easing: Literal["linear", "ease_in_out"] = "linear"


class Overlay(BaseModel):
    """Lay another clip over the picture, at a stated place and time.

    Placement is in PIXELS of the edit's own frame. Percentages and an animated
    geometry track are deliberately not here yet: an inset that grows to full
    screen is a property of a MOTION over time, and inventing a syntax for it
    before that lands would mean two ways to say the same thing.

    Sound is not taken from the layer. An overlay that quietly added a second
    audio track would duplicate narration in the common picture-in-picture case,
    where the base already carries it.
    """

    op: Literal["overlay"] = "overlay"
    source: str
    x: int = 0
    y: int = 0
    # None means the layer's own size, untouched. Both must be given together:
    # one alone would have to invent the other from an aspect ratio nobody
    # stated, which is how footage gets silently reshaped.
    width: int | None = None
    height: int | None = None
    # The window the layer is on screen for, in seconds. None for either end
    # means "from the beginning" and "until the end" respectively.
    start: float | None = None
    end: float | None = None
    # What the layer is cut to. None is the whole rectangle.
    mask: Mask | None = None
    # Where the layer travels to. None holds the stated geometry throughout.
    motion: Motion | None = None
    # What happens to the layer's own sound. None means `drop`.
    audio: LayerAudio | None = None
    # Make part of the layer's own picture transparent. None keys nothing.
    key: Key | None = None
    # Uniform transparency, 0..1. 1.0 is fully opaque and a genuine no-op.
    opacity: float = 1.0


class Stitch(BaseModel):
    """Append other sources. `-` stands for the plan arriving on stdin."""

    op: Literal["stitch"] = "stitch"
    sources: list[str]
    # How a clip that is not the target size is resolved. `concat` requires
    # matching resolution and SAR, so a mismatch is otherwise rejected by ffmpeg
    # at render time. None means the caller has not chosen, and a mismatch is
    # refused rather than normalised: auto-fitting would change someone's
    # framing without saying so, which is the failure this field exists to
    # prevent. Aspect ratio is preserved by both modes.
    #   fit   pad the remainder with bars; nothing leaves frame
    #   fill  crop the overflow centred; nothing is letterboxed
    fit: Literal["fit", "fill"] | None = None
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


class AudioRemove(BaseModel):
    """Drop the audio track. The result is a silent video."""

    op: Literal["audio_remove"] = "audio_remove"


class AudioReplace(BaseModel):
    """Swap the audio track for another file's.

    LENGTH IS ALWAYS THE VIDEO'S. A shorter track is padded with silence, a
    longer one is truncated. Stated here because it is the question every caller
    asks, and inheriting ffmpeg's default would leave the answer to accident.
    """

    op: Literal["audio_replace"] = "audio_replace"
    track: str
    start: float = Field(default=0.0, ge=0, allow_inf_nan=False)


class AudioMix(BaseModel):
    """Lay another track UNDER the existing audio, keeping both.

    `level` is applied to the incoming track only, in dB, and is negative in
    normal use -- a music bed sits below speech. The existing audio is untouched,
    so a caller adjusts one thing rather than balancing two.

    No ducking. A flat level is predictable and explicable; automatic ducking is
    a real feature with its own decisions and should be asked for on purpose.
    """

    op: Literal["audio_mix"] = "audio_mix"
    track: str
    level: float = -18.0
    start: float = Field(default=0.0, ge=0, allow_inf_nan=False)


class Recolor(BaseModel):
    """Map the video's colour onto a reference image's.

    THE PLAN CARRIES THE MEASUREMENTS, NOT A FILE PATH. Six numbers and a
    strength, rather than a pointer to a lookup table somewhere on this machine.
    A plan is meant to be saved, diffed, mailed and replayed, and a plan that
    depends on a temp file is a plan that stops working tomorrow.

    The lookup table is regenerated from these numbers at render. It is pure
    arithmetic, it takes no time worth measuring, and the result is identical
    every time -- so there is nothing to gain by keeping the file and everything
    to gain by keeping the plan self-contained.
    """

    op: Literal["recolor"] = "recolor"
    reference: str
    source_mean: tuple[float, float, float]
    source_std: tuple[float, float, float]
    reference_mean: tuple[float, float, float]
    reference_std: tuple[float, float, float]
    strength: float = 1.0


class Vignette(BaseModel):
    """Darken the edges, to pull an eye to the middle."""

    op: Literal["vignette"] = "vignette"
    strength: float = 0.35


class Grade(BaseModel):
    """A named look, applied to the whole clip."""

    op: Literal["grade"] = "grade"
    look: str


class Lut(BaseModel):
    """Apply a .cube lookup table you already have."""

    op: Literal["lut"] = "lut"
    path: str


Operation = Annotated[
    Trim
    | Cut
    | Retime
    | Zoom
    | Stitch
    | Overlay
    | Caption
    | AudioRemove
    | AudioReplace
    | AudioMix
    | Recolor
    | Vignette
    | Grade
    | Lut,
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

    try:
        return Plan.model_validate(data)
    except ValidationError as exc:
        raise VidError(
            f"That plan claims format {PLAN_FORMAT} but does not match its shape:\n{exc}\n"
            "Rebuild it with the verb that produces this stage rather than hand-editing the JSON."
        ) from exc


def write_plan(plan: Plan) -> None:
    """The plan, to stdout, for the next verb in the pipe."""
    sys.stdout.write(plan.model_dump_json(indent=2, exclude_none=False) + "\n")
