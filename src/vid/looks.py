"""Named looks, as filter chains.

Everything else in this tool changes WHICH frames you see and WHEN. These change
how they LOOK, which for a tool whose job is preparing recordings for an audience
is not decoration -- a raw screen capture is rarely the thing you want to show.

NAMED RATHER THAN PARAMETERISED, deliberately. `--warm` is one decision a caller
can make in a second; `--warmth 0.3 --contrast 1.1 --saturation 1.2` is four
decisions they have no basis for. Anyone who genuinely wants numbers already has
`vid lut` for a .cube file and `vid recolor` for matching a reference image, both
of which express intent far better than a pile of dials.
"""

from __future__ import annotations

from vid.schemas import VidError

# HOW THESE STRENGTHS WERE CHOSEN, because the first set was wrong and looked
# right. The originals used colorbalance weights around 0.06-0.08, which READ as
# reasonable and measured as nothing: a Lab b* shift of +0.23 for `warm` against
# a source at 6.30. Directionally correct, practically invisible.
#
# Measuring caught it; reading the numbers never would have. They are now in the
# 0.14-0.22 range, and the test asserts a MINIMUM shift rather than merely a
# direction -- because "it moved the right way" was already true of the version
# nobody would have noticed.
#
# A second lesson, about the FIXTURE rather than the values: a flat neutral grey
# measures every look as identical, because colorbalance weights shadows,
# midtones and highlights separately and a flat frame has only one tone. Grades
# must be measured on content with actual tonal range.

#: Each look is an ffmpeg filter chain, with a sentence saying what it is FOR
#: rather than what it does -- a caller choosing between them needs the use, not
#: the parameters.
LOOKS: dict[str, tuple[str, str]] = {
    "warm": (
        "colorbalance=rs=.22:gs=.06:bs=-.18:rm=.18:gm=.04:bm=-.14:rh=.10:bh=-.08,eq=saturation=1.15",
        "Pushes toward amber. Makes a cold monitor capture look like a room.",
    ),
    "cool": (
        "colorbalance=rs=-.18:bs=.22:rm=-.14:bm=.18:rh=-.08:bh=.10,eq=saturation=1.08",
        "Pushes toward blue. Clean and clinical, good for technical material.",
    ),
    "punchy": (
        "eq=contrast=1.18:saturation=1.3:gamma=0.96",
        "More contrast and colour. For footage that looks flat on a projector.",
    ),
    "flat": (
        "eq=contrast=0.88:saturation=0.82:gamma=1.04",
        "Takes the edge off. Useful under captions, or behind a talking head.",
    ),
    "noir": (
        "hue=s=0,eq=contrast=1.25:gamma=0.94",
        "Black and white with hard contrast.",
    ),
    "soft": (
        "eq=contrast=0.95:brightness=0.03:saturation=0.95,gblur=sigma=0.6",
        "Slightly lifted and gently blurred. Hides compression noise.",
    ),
    "bright": (
        "eq=brightness=0.06:contrast=1.06",
        "Lifts an underexposed capture without washing the colour out.",
    ),
}


def resolve_look(name: str) -> str:
    """The filter chain for a named look, or a refusal that lists the names."""
    cleaned = name.strip().lower()
    if cleaned in LOOKS:
        return LOOKS[cleaned][0]
    raise VidError(
        f"There is no look called {name!r}. The names are: {', '.join(sorted(LOOKS))}.\n"
        "Run `vid grade --list` to see what each one is for. For something more "
        "specific, `vid recolor --like <image>` matches a reference picture, and "
        "`vid lut <file.cube>` applies a table you already have."
    )


def catalogue() -> str:
    width = max(len(name) for name in LOOKS)
    return "\n".join(
        f"  {name:<{width}}  {purpose}" for name, (_, purpose) in sorted(LOOKS.items())
    )


def vignette_filter(strength: float) -> str:
    """ffmpeg's vignette, driven by one number instead of an angle in radians.

    The filter takes an ANGLE, where a bigger angle means a darker edge, and
    nobody thinks about a vignette in radians. `strength` runs 0 to 1 and maps
    onto the useful part of that range: 0.35 is the default because it reads on a
    projector without announcing itself, which is the whole job.
    """
    if not 0.0 <= strength <= 1.0:
        raise VidError(f"--strength must be between 0 and 1; got {strength}.")
    # PI/12 is barely visible, PI/2.4 is theatrical. The default lands near PI/5.
    angle = 0.2618 + strength * 0.8472
    return f"vignette=angle={angle:.4f}"
