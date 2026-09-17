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
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite", "radial",
    "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "vertopen", "vertclose", "horzopen", "horzclose",
    "dissolve", "pixelize", "diagtl", "diagtr", "diagbl", "diagbr",
    "hlslice", "hrslice", "vuslice", "vdslice", "hblur", "fadegrays",
    "wipetl", "wipetr", "wipebl", "wipebr", "squeezeh", "squeezev", "zoomin",
    "fadefast", "fadeslow", "hlwind", "hrwind", "vuwind", "vdwind",
    "coverleft", "coverright", "coverup", "coverdown",
    "revealleft", "revealright", "revealup", "revealdown",
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
            raise VidError(
                f"There is no transition called {text!r}. Did you mean {near!r}? "
                f"({FEEL.get(near, '')})"
            )
        raise VidError(
            f"There is no transition called {text!r}, and it is one word, so it reads as a name "
            f"rather than a description. Run `vid transitions` for the full list, or describe "
            f"what you want in a phrase -- `--transition \"soft and dreamy\"` -- and a model will pick."
        )

    if intelligence is None:
        raise VidError(
            f"{text!r} is a description, and choosing a preset from it needs a model that is not "
            f"configured. Either name one of the {len(PRESETS)} presets directly "
            f"(`vid transitions` lists them), or configure a provider -- `vid check` says how."
        )

    return _select(text, intelligence)


def _select(description: str, intelligence) -> tuple[str, str, str]:
    """Ask a model to pick, and refuse anything that is not on the list."""
    from vid.intelligence.schemas import AgentRequest

    request = AgentRequest(
        instruction=(
            "Choose the ONE transition whose motion best matches the description.\n\n"
            f"DESCRIPTION: {description}\n\n"
            f"THE ONLY VALID ANSWERS:\n{catalogue()}\n\n"
            "Reply with the preset name on the first line and one short sentence of "
            "reasoning on the second. Nothing else. The name must be copied exactly "
            "from the list above."
        )
    )
    result = intelligence.run(request)
    reply = (getattr(result, "output", None) or getattr(result, "text", "") or "").strip()
    if not reply:
        raise VidError(f"The model returned nothing when asked to choose a transition for {description!r}.")

    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    choice = lines[0].strip().strip("`\"'").lower()
    rationale = lines[1] if len(lines) > 1 else ""

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
