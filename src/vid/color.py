"""Take the palette from an image and map a video onto it.

"Make this recording look like that photograph." A reference image is the only
unambiguous way to say what you want -- a named look like `--warm` is a guess at
somebody's intent, while a picture IS the intent.

HOW IT WORKS. Reinhard colour transfer: convert both to a perceptual space,
measure each channel's mean and spread, and derive the affine map that moves the
source's distribution onto the reference's. Old, well understood, and pure
arithmetic. It produces a natural GRADE, not a poster.

WHY IT COSTS ONE DECODE LIKE EVERYTHING ELSE. The transform is NOT computed per
frame. It is baked once into a 3D lookup table and handed to ffmpeg's `lut3d`,
which applies it to every frame as part of the same single pass as trim, zoom and
the rest.

AND IT NEEDS NOTHING INSTALLED. A `.cube` LUT is plain text, so generating one
takes no image library, no numpy, no provider, and no network. A rich capability
with no AI in it at all -- which is the point: the smart tiers stay reserved for
what actually needs judgment.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import subprocess

from vid.schemas import VidError

#: Frames sampled from a video to measure its colour. Nine, spread evenly and
#: skipping the very ends: one frame lets a single dark shot decide the whole
#: grade, and titles or fades at the extremes are unrepresentative of the body.
SAMPLE_FRAMES = 9

#: Cube root of the lookup table. 17^3 is 4913 entries -- plenty for a smooth
#: grade, small enough that the file is a few hundred kilobytes of text and
#: generating it is instant.
LUT_SIZE = 17

#: Sampled frames are read at this width. Colour statistics are unchanged by
#: resolution, and a full-size frame costs real time to compute an identical
#: number.
SAMPLE_WIDTH = 160


@dataclass(frozen=True)
class ColorStats:
    """Mean and spread per channel, in Lab."""

    mean: tuple[float, float, float]
    std: tuple[float, float, float]

    def distance_to(self, other: ColorStats) -> float:
        """How far apart two palettes are. Used to PROVE a grade did something."""
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(self.mean, other.mean, strict=True)))


# --- colour space ---------------------------------------------------------
# sRGB -> linear -> XYZ -> Lab, and back. Written out rather than imported
# because pulling a colour-science package in for twenty lines of arithmetic
# would add an install dependency to the one capability here that has none.

_WHITE = (0.95047, 1.00000, 1.08883)


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _from_linear(c: float) -> float:
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def rgb_to_lab(r: float, g: float, b: float) -> tuple[float, float, float]:
    r, g, b = _to_linear(r), _to_linear(g), _to_linear(b)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / _WHITE[0]
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / _WHITE[1]
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / _WHITE[2]

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t) + (16 / 116)

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def lab_to_rgb(lightness: float, a: float, b: float) -> tuple[float, float, float]:
    fy = (lightness + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200

    def finv(t: float) -> float:
        return t**3 if t**3 > 0.008856 else (t - 16 / 116) / 7.787

    x, y, z = finv(fx) * _WHITE[0], finv(fy) * _WHITE[1], finv(fz) * _WHITE[2]
    r = 3.2406 * x - 1.5372 * y - 0.4986 * z
    g = -0.9689 * x + 1.8758 * y + 0.0415 * z
    bl = 0.0557 * x - 0.2040 * y + 1.0570 * z
    return tuple(min(1.0, max(0.0, _from_linear(c))) for c in (r, g, bl))  # type: ignore[return-value]


# --- measuring ------------------------------------------------------------


def _raw_rgb(command: list[str]) -> bytes:
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        detail = (result.stderr or b"").decode(errors="replace").strip().splitlines()
        raise VidError(f"Could not read pixels: {detail[-1] if detail else 'ffmpeg produced nothing'}")
    return result.stdout


def _stats_from_pixels(raw: bytes) -> ColorStats:
    """Lab mean and standard deviation over raw rgb24 bytes."""
    count = len(raw) // 3
    if count == 0:
        raise VidError("No pixels to measure.")

    # Every 7th pixel: colour statistics converge long before every pixel has
    # been visited, and Lab conversion is the expensive part of this loop.
    step = max(1, count // 20000)
    sums = [0.0, 0.0, 0.0]
    squares = [0.0, 0.0, 0.0]
    taken = 0
    for index in range(0, count, step):
        offset = index * 3
        lab = rgb_to_lab(raw[offset] / 255, raw[offset + 1] / 255, raw[offset + 2] / 255)
        for channel in range(3):
            sums[channel] += lab[channel]
            squares[channel] += lab[channel] ** 2
        taken += 1

    means = [total / taken for total in sums]
    stds = [math.sqrt(max(0.0, squares[c] / taken - means[c] ** 2)) for c in range(3)]
    return ColorStats(mean=tuple(means), std=tuple(stds))  # type: ignore[arg-type]


def measure_image(path: str) -> ColorStats:
    return _stats_from_pixels(
        _raw_rgb(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                path,
                "-vf",
                f"scale={SAMPLE_WIDTH}:-2",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ]
        )
    )


def measure_video(path: str, duration: float | None = None) -> ColorStats:
    """Sample frames spread through a video and measure them together."""
    if duration is None:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
            capture_output=True,
            text=True,
        )
        try:
            duration = float(probe.stdout.strip())
        except ValueError as exc:
            raise VidError(f"{path!r} has no readable duration.") from exc

    collected = bytearray()
    for index in range(SAMPLE_FRAMES):
        # Skip the first and last tenth: titles and fades are not the body.
        at = duration * (0.1 + 0.8 * index / max(1, SAMPLE_FRAMES - 1))
        try:
            collected += _raw_rgb(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    f"{at:.3f}",
                    "-i",
                    path,
                    "-vf",
                    f"scale={SAMPLE_WIDTH}:-2",
                    "-frames:v",
                    "1",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-",
                ]
            )
        except VidError:
            continue
    if not collected:
        raise VidError(f"Could not sample any frame from {path!r}.")
    return _stats_from_pixels(bytes(collected))


# --- the transform --------------------------------------------------------


def write_cube(source: ColorStats, reference: ColorStats, out: Path | str, strength: float = 1.0) -> Path:
    """Bake the transfer into a 3D lookup table ffmpeg can apply.

    A `.cube` file is plain TEXT, which is why this whole capability needs
    nothing installed. The alternative -- computing the map per frame -- would
    have meant an image library, a per-frame cost, and a second decode.

    `strength` blends toward the identity. A full transfer onto a very different
    reference can look ridiculous, and the honest control for that is a dial the
    caller can turn rather than a clamp nobody can see.
    """
    if not 0.0 <= strength <= 1.0:
        raise VidError(f"--strength must be between 0 and 1; got {strength}.")

    scales = []
    for channel in range(3):
        src_spread = source.std[channel]
        # A nearly flat channel has no spread to rescale, and dividing by it
        # would explode. A solid-colour reference is a real thing to point at.
        scales.append(reference.std[channel] / src_spread if src_spread > 1e-4 else 1.0)

    out = Path(out)
    lines = [
        "# Generated by vid -- Reinhard colour transfer in Lab.",
        f"# strength {strength}",
        f"LUT_3D_SIZE {LUT_SIZE}",
        "",
    ]
    step = 1.0 / (LUT_SIZE - 1)
    # .cube iterates RED fastest, then green, then blue.
    for blue_index in range(LUT_SIZE):
        for green_index in range(LUT_SIZE):
            for red_index in range(LUT_SIZE):
                r, g, b = red_index * step, green_index * step, blue_index * step
                lab = rgb_to_lab(r, g, b)
                moved = tuple((lab[c] - source.mean[c]) * scales[c] + reference.mean[c] for c in range(3))
                blended = tuple(lab[c] + (moved[c] - lab[c]) * strength for c in range(3))
                nr, ng, nb = lab_to_rgb(*blended)
                lines.append(f"{nr:.6f} {ng:.6f} {nb:.6f}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
