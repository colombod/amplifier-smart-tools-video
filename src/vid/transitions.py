"""How a transition is chosen.

Three tiers, one flag, and the tier is decided by what the caller wrote.

  --transition dissolve            a name. Deterministic, $0.00, no provider.
  --transition "soft and dreamy"   a description. A model PICKS from the list below.
  --transition "slam in from the   a description no preset matches. A model would
                right, overshoot"  WRITE an expression -- not built yet, see the
                                   `verify` work before trusting generated motion.

THE GUARD, and it is the same one as everywhere else in this tool: a model
selecting from a CLOSED SET cannot invent an answer. It returns one of 58 names
or it returns something that is not a transition, and the second case is a loud
error rather than a plausible wrong one. Compare the third tier, where the model
writes an arbitrary expression and nothing bounds it -- which is exactly why that
tier waits for property-based verification.

WHERE THE MODEL RUNS MATTERS MORE THAN THAT IT RUNS. It runs ONCE, when the plan
is built, and the plan records the RESOLVED preset alongside what was asked for.
Re-rendering a plan never calls a model again. So an edit stays reproducible, a
human can read the choice before a frame is touched, and the same plan renders
identically on a machine with no credentials at all.
"""

from __future__ import annotations

import difflib

from vid.schemas import VidError

#: ffmpeg's xfade vocabulary. Checked against `ffmpeg -h filter=xfade`; a build
#: without one of these fails at render with ffmpeg's own message, which is
#: clearer than anything we would invent.
PRESETS: tuple[str, ...] = (
    "fade",
    "wipeleft",
    "wiperight",
    "wipeup",
    "wipedown",
    "slideleft",
    "slideright",
    "slideup",
    "slidedown",
    "circlecrop",
    "rectcrop",
    "distance",
    "fadeblack",
    "fadewhite",
    "radial",
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
    "circleopen",
    "circleclose",
    "vertopen",
    "vertclose",
    "horzopen",
    "horzclose",
    "dissolve",
    "pixelize",
    "diagtl",
    "diagtr",
    "diagbl",
    "diagbr",
    "hlslice",
    "hrslice",
    "vuslice",
    "vdslice",
    "hblur",
    "fadegrays",
    "wipetl",
    "wipetr",
    "wipebl",
    "wipebr",
    "squeezeh",
    "squeezev",
    "zoomin",
    "fadefast",
    "fadeslow",
    "hlwind",
    "hrwind",
    "vuwind",
    "vdwind",
    "coverleft",
    "coverright",
    "coverup",
    "coverdown",
    "revealleft",
    "revealright",
    "revealup",
    "revealdown",
)

#: What each one LOOKS like, for a model choosing among them and for a person
#: reading `--help`. A bare name tells a caller nothing about the motion.
FEEL: dict[str, str] = {
    "fade": "a straightforward crossfade; the safe default",
    "dissolve": "a grainy, textured crossfade; softer and less clinical than fade",
    "fadeblack": "through black; reads as a scene break or passage of time",
    "fadewhite": "through white; brighter, more upbeat than fadeblack",
    "fadegrays": "desaturates to grey and back; nostalgic, dreamlike",
    "fadefast": "a crossfade weighted to finish quickly; energetic",
    "fadeslow": "a crossfade weighted to linger; contemplative",
    "distance": "pixels blend by colour distance; strange and organic",
    "radial": "sweeps around like a clock hand",
    "pixelize": "both sides break into blocks and reform; digital, glitchy",
    "hblur": "blurs out and back in; a soft, expensive-looking bridge",
    "zoomin": "the incoming clip pushes forward into frame; dynamic",
    "squeezeh": "squashed horizontally; playful, cartoonish",
    "squeezev": "squashed vertically; playful, cartoonish",
    "circleopen": "an expanding circle reveals the next clip; classic, cinematic",
    "circleclose": "a closing circle; an iris-out, an ending",
    "circlecrop": "a circular crop through black",
    "rectcrop": "a rectangular crop through black",
    "wipeleft": "a hard edge sweeps leftward",
    "wiperight": "a hard edge sweeps rightward",
    "wipeup": "a hard edge sweeps upward",
    "wipedown": "a hard edge sweeps downward",
    "slideleft": "the next clip slides in from the right, pushing the old one out",
    "slideright": "the next clip slides in from the left, pushing the old one out",
    "slideup": "the next clip slides up from below",
    "slidedown": "the next clip slides down from above",
    "coverleft": "the next clip slides over the top, leftward; the old one stays put",
    "coverright": "the next clip slides over the top, rightward",
    "coverup": "the next clip slides over the top, upward",
    "coverdown": "the next clip slides over the top, downward",
    "revealleft": "the old clip slides away leftward, revealing what was behind",
    "revealright": "the old clip slides away rightward",
    "revealup": "the old clip slides away upward",
    "revealdown": "the old clip slides away downward",
    "smoothleft": "a soft-edged wipe leftward; gentler than wipeleft",
    "smoothright": "a soft-edged wipe rightward",
    "smoothup": "a soft-edged wipe upward",
    "smoothdown": "a soft-edged wipe downward",
    "vertopen": "opens from the centre outward, vertically, like doors",
    "vertclose": "closes inward to the centre, vertically",
    "horzopen": "opens from the centre outward, horizontally",
    "horzclose": "closes inward to the centre, horizontally",
    "diagtl": "a diagonal wipe from the top-left corner",
    "diagtr": "a diagonal wipe from the top-right corner",
    "diagbl": "a diagonal wipe from the bottom-left corner",
    "diagbr": "a diagonal wipe from the bottom-right corner",
    "hlslice": "horizontal slices sweep in from the left; graphic, editorial",
    "hrslice": "horizontal slices sweep in from the right",
    "vuslice": "vertical slices sweep upward",
    "vdslice": "vertical slices sweep downward",
    "hlwind": "a windswept dissolve moving left; atmospheric",
    "hrwind": "a windswept dissolve moving right",
    "vuwind": "a windswept dissolve moving up",
    "vdwind": "a windswept dissolve moving down",
    "wipetl": "a corner wipe from top-left",
    "wipetr": "a corner wipe from top-right",
    "wipebl": "a corner wipe from bottom-left",
    "wipebr": "a corner wipe from bottom-right",
}


def is_preset(text: str) -> bool:
    return text.strip().lower() in PRESETS


def looks_like_a_description(text: str) -> bool:
    """A description has words in it. A misspelled preset does not.

    This distinction is the whole dispatch, so it is deliberately dumb: a single
    token is meant to be a name, and a near-miss should be CORRECTED rather than
    quietly handed to a model that would cheerfully interpret `disolve` as a
    request and charge for it.
    """
    return len(text.split()) > 1


def suggest(text: str) -> str | None:
    matches = difflib.get_close_matches(text.strip().lower(), PRESETS, n=1, cutoff=0.7)
    return matches[0] if matches else None


def catalogue() -> str:
    """Every preset with what it looks like -- the list a model chooses from."""
    return "\n".join(f"{name}: {FEEL.get(name, 'no description recorded')}" for name in PRESETS)


def resolve(text: str, intelligence=None) -> tuple[str, str | None, str | None]:
    """A caller's `--transition` value, resolved to a preset.

    Returns `(preset, requested, rationale)`. `requested` and `rationale` are None
    when no model was involved, which is how the plan records that a choice was
    deterministic.
    """
    cleaned = text.strip().lower()

    if cleaned in PRESETS:
        return cleaned, None, None

    if not looks_like_a_description(text):
        near = suggest(cleaned)
        if near:
            raise VidError(f"There is no transition called {text!r}. Did you mean {near!r}? ({FEEL.get(near, '')})")
        raise VidError(
            f"There is no transition called {text!r}, and it is one word, so it reads as a name "
            f"rather than a description. Run `vid transitions` for the full list, or describe "
            f'what you want in a phrase -- `--transition "soft and dreamy"` -- and a model will pick.'
        )

    if intelligence is None:
        raise VidError(
            f"{text!r} is a description, and choosing a preset from it needs a model that is not "
            f"configured. Either name one of the {len(PRESETS)} presets directly "
            f"(`vid transitions` lists them), or configure a provider -- `vid check` says how."
        )

    return _select(text, intelligence)


def _select(description: str, intelligence, *, allow_none: bool = False):
    """Ask a model to pick, and refuse anything that is not on the list.

    With `allow_none`, the model may report that nothing in the 58 fits -- which
    is the ONLY route to tier 3. Without it, a non-match is an error, which is
    the right behaviour when generation is not available.
    """
    from vid.intelligence import ask

    reply = ask(
        intelligence,
        (
            "Choose the ONE transition whose motion best matches the description.\n\n"
            f"DESCRIPTION: {description}\n\n"
            f"THE ONLY VALID ANSWERS:\n{catalogue()}\n\n"
            "Reply with the preset name on the first line and one short sentence of "
            "reasoning on the second. Nothing else. The name must be copied exactly "
            "from the list above."
            + (
                "\n\nIf NONE of these genuinely matches the described motion, reply "
                "with the single word NONE on the first line instead. Do not stretch "
                "a preset to fit -- answering NONE is how a new transition gets "
                "written, and a forced match is worse than an honest miss."
                if allow_none
                else ""
            )
        ),
    )

    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    choice = lines[0].strip().strip("`\"'").lower()
    rationale = lines[1] if len(lines) > 1 else ""

    if allow_none and choice.upper().startswith("NONE"):
        return None, description, rationale

    # THE GUARD. A closed set means a wrong answer is DETECTABLE, which is the
    # entire reason this tier is safe and the generate-an-expression tier is not.
    if choice not in PRESETS:
        near = suggest(choice)
        hint = f" The closest real one is {near!r}." if near else ""
        raise VidError(
            f"The model answered {choice!r}, which is not one of the {len(PRESETS)} transitions "
            f"ffmpeg has.{hint} Name a preset directly rather than accepting a guess."
        )
    return choice, description, rationale


# ---------------------------------------------------------------------------
# TIER 3: generate an expression, then refuse to trust it until it is proven.
# ---------------------------------------------------------------------------

#: The expression vocabulary, plus the ONE THING the experiment showed a model
#: gets wrong unaided. This block is the tool's actual expertise -- a model given
#: only "write an xfade expression" produced code that reads as obviously correct
#: and is wrong, because it assumed RGB. Carrying this warning is the difference
#: between a coin flip and a capability.
EXPRESSION_GUIDE = """\
`xfade` with `transition=custom` takes an `expr` evaluated for every pixel.

Variables available:
  P       progress through the transition, 0.0 at the start to 1.0 at the end
  X, Y    pixel coordinates; X is 0 at the left, Y is 0 at the TOP
  W, H    width and height of the frame
  A       the pixel value from the OUTGOING clip
  B       the pixel value from the INCOMING clip
  PLANE   which image plane is being computed -- see the warning below

Functions: if(cond,a,b), lt, gt, lte, gte, eq, min, max, abs, hypot, sin, cos,
sqrt, pow, floor, between(x,lo,hi).

CRITICAL, AND MOST EXPRESSIONS GET THIS WRONG:
The expression is evaluated ONCE PER YUV PLANE, not over RGB. PLANE 0 is luma
(brightness, 0 = black). PLANES 1 and 2 are chroma, where NEUTRAL IS 128, not 0.

So `A*(1-P)` does NOT fade to black. It drives luma toward black AND chroma
toward extreme colour. To fade toward black you must treat the planes
differently:

  if(eq(PLANE,0), A*(1-P), 128+(A-128)*(1-P))

A plain crossfade is unaffected by this, because blending two pixels is linear
in every plane:  A*(1-P)+B*P

Geometric transitions -- wipes, slides, reveals -- are also unaffected, because
they CHOOSE between A and B rather than scaling either:
  a wipe from the right:  if(gt(X, W*(1-P)), B, A)
"""


def generate(description: str, intelligence) -> tuple[str, str]:
    """Ask a model to WRITE a transition. Returns `(expression, rationale)`.

    Unbounded output, unlike tier 2 -- which is exactly why nothing here trusts
    the result. The caller must prove it before it reaches a plan.
    """
    from vid.intelligence import ask

    reply = ask(
        intelligence,
        (
            "Write an ffmpeg xfade `custom` transition expression for this description.\n\n"
            f"DESCRIPTION: {description}\n\n"
            f"{EXPRESSION_GUIDE}\n"
            "Reply with the expression alone on the first line -- no quotes, no "
            "`expr=`, no explanation on that line -- and one short sentence of "
            "reasoning on the second line. Nothing else."
        ),
        timeout_seconds=90,
    )

    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    expression = lines[0].strip().strip("`\"'")
    if expression.startswith("expr="):
        expression = expression[5:].strip().strip("'\"")
    rationale = lines[1] if len(lines) > 1 else ""

    if not expression or len(expression) > 2000:
        raise VidError(
            f"The model did not return a usable expression for {description!r}. Retry, or "
            "name one of the 58 presets instead (`vid transitions`)."
        )
    # A refusal that reaches ffmpeg becomes an unreadable parse error; catching
    # the obvious shapes here keeps the message about what actually happened.
    if not any(token in expression for token in ("A", "B", "P")):
        raise VidError(
            f"The model answered {expression[:80]!r}, which references neither input "
            "nor progress, so it is not a transition expression. Retry, or name one of "
            "the 58 presets instead (`vid transitions`)."
        )
    return expression, rationale


#: How long a probe clip is, per side. Enough to sample either side of the blend
#: and no more -- the probe exists to answer one question, not to preview an edit.
PROBE_SECONDS = 1.5

#: Probes render at this width. The blend checks read AVERAGE frame colour, which
#: survives downscaling completely, so a full-resolution probe would cost real
#: time to compute a number that does not change.
PROBE_WIDTH = 160


def probe(expression: str, first: str, second: str, duration: float) -> tuple[bool, str]:
    """Render a generated transition and find out whether it actually blends.

    THE GATE. A model's expression is unbounded output -- it can compile and
    still move wrongly, which is exactly what happened once in six during the
    experiment that justified this feature. So nothing trusts it until it has
    been rendered and measured.

    Returns `(passed, detail)`. `detail` is what to tell the caller when it fails.
    """
    from pathlib import Path
    import subprocess
    import tempfile

    from vid import verify as checks

    with tempfile.TemporaryDirectory(prefix="vid-probe-") as work:
        out = str(Path(work) / "probe.mp4")
        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-t",
            str(PROBE_SECONDS),
            "-i",
            first,
            "-t",
            str(PROBE_SECONDS),
            "-i",
            second,
            "-filter_complex",
            (
                f"[0:v]scale={PROBE_WIDTH}:-2,setsar=1[a];"
                f"[1:v]scale={PROBE_WIDTH}:-2,setsar=1[b];"
                f"[a][b]xfade=transition=custom:duration={duration}"
                f":offset={max(0.0, PROBE_SECONDS - duration)}:expr='{expression}'[v]"
            ),
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            out,
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()
            reason = tail[-1][:200] if tail else "ffmpeg gave no reason"
            return False, f"it does not compile -- ffmpeg said: {reason}"

        midpoint = max(0.0, PROBE_SECONDS - duration) + duration / 2
        blend = checks.check_transition_at(out, midpoint, window=duration / 2 + 0.25)
        if not blend.held:
            return False, f"it compiles but does not blend -- {blend.measured}. {blend.note}".strip()
        return True, blend.measured


def resolve_with_clips(
    text: str,
    first: str | None,
    second: str | None,
    duration: float,
    intelligence=None,
) -> tuple[str, str | None, str | None, str | None]:
    """The full ladder. Returns `(preset, requested, rationale, expression)`.

    Tier 2 runs FIRST, always. Its answer is bounded by a closed set and it costs
    one cheap call, so a caller never pays for generation when one of the 58
    presets would have done. Only when the model reports that nothing fits does
    tier 3 write something new -- and that is then proven before it is used.
    """
    cleaned = text.strip().lower()
    if cleaned in PRESETS:
        return cleaned, None, None, None

    if not looks_like_a_description(text):
        near = suggest(cleaned)
        if near:
            raise VidError(f"There is no transition called {text!r}. Did you mean {near!r}? ({FEEL.get(near, '')})")
        raise VidError(
            f"There is no transition called {text!r}, and it is one word, so it reads as a name "
            f"rather than a description. Run `vid transitions` for the full list, or describe "
            f'what you want in a phrase -- --transition "soft and dreamy" -- and a model will pick.'
        )

    if intelligence is None:
        raise VidError(
            f"{text!r} is a description, and choosing a preset from it needs a model that is not "
            f"configured. Either name one of the {len(PRESETS)} presets directly "
            f"(`vid transitions` lists them), or configure a provider -- `vid check` says how."
        )

    choice, requested, rationale = _select(text, intelligence, allow_none=True)
    if choice is not None:
        return choice, requested, rationale, None

    # Nothing in the closed set fits. This is the only route to generation.
    if not (first and second):
        raise VidError(
            f"No preset matches {text!r}, and writing a new transition requires the clips "
            "it will join, so the tool can prove the result actually blends. Name the clips "
            "in the same command rather than piping a plan in."
        )

    expression, why = generate(text, intelligence)
    passed, detail = probe(expression, first, second, duration)
    if not passed:
        raise VidError(
            f"A transition was written for {text!r}, and it did not survive checking: {detail}\n"
            f"  expression: {expression}\n"
            "Refusing rather than rendering it. Name one of the 58 presets instead "
            "(`vid transitions`), or describe the motion differently."
        )
    return "custom", text, (why or None), expression
