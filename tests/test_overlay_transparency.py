"""Uniform opacity, colour keying, and the order the two combine in.

The `keyable` fixture is a green field with a blue bar across the FULL width,
and that width is the whole point. A first probe used a 200x200 centred subject
with a centred circle mask -- but the subject sat entirely inside the circle, so
"key only" and "key plus mask" produced identical pixels everywhere sampled. It
proved nothing about the mask.

The bar's ends stick out past a centred circle, so:

    (320, 180)  inside the bar AND inside the circle
    (30, 180)   inside the bar, OUTSIDE the circle   <- the discriminating point
    (320, 40)   green field, keyed away in every case

Without that third geometry the intersection test is decoration.
"""

import pytest

from tests.fixtures import ensure_clips, ensure_keyable_clip, have_ffmpeg, pixel_at
from vid.lib import render
from vid.plan import Key, Mask, Overlay, Plan
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg is required")

BAR_CENTRE = (320, 180)
BAR_FAR_LEFT = (30, 180)
GREEN_FIELD = (320, 40)


@pytest.fixture(scope="module")
def clips():
    return ensure_clips()


@pytest.fixture(scope="module")
def keyable():
    return ensure_keyable_clip()


def _is_base(colour):
    return colour[0] > 150 and colour[1] < 110


def _is_bar(colour):
    return colour[2] > 120 and colour[0] < 120


def _composite(clips, tmp_path, name, layer, **fields):
    plan = Plan(source=str(clips["alpha"].path)).with_operation(Overlay(source=str(layer), **fields))
    return render(plan, str(tmp_path / f"{name}.mp4"))


def test_colorkey_removes_the_field_and_keeps_the_subject(clips, keyable, tmp_path):
    output = _composite(clips, tmp_path, "keyed", keyable.path, key=Key(kind="colorkey", colour="0x00FF00"))

    assert _is_bar(pixel_at(output, 1.0, *BAR_CENTRE)), "the key removed the subject it should have kept"
    assert _is_base(pixel_at(output, 1.0, *GREEN_FIELD)), "the keyed colour did not become transparent"


def test_chromakey_also_removes_the_field(clips, keyable, tmp_path):
    output = _composite(clips, tmp_path, "chroma", keyable.path, key=Key(kind="chromakey", colour="0x00FF00"))

    assert _is_bar(pixel_at(output, 1.0, *BAR_CENTRE))
    assert _is_base(pixel_at(output, 1.0, *GREEN_FIELD))


def test_a_key_and_a_mask_intersect(clips, keyable, tmp_path):
    """The contract: what survives is inside the key AND inside the shape.

    Asserted at the far-left point, where the bar exists but the circle does
    not. A union, or a mask that silently replaced the key's alpha, would show
    the bar there.
    """
    keyed_only = _composite(clips, tmp_path, "key_only", keyable.path, key=Key(kind="colorkey"))
    both = _composite(clips, tmp_path, "key_and_mask", keyable.path, key=Key(kind="colorkey"), mask=Mask(kind="circle"))

    assert _is_bar(pixel_at(keyed_only, 1.0, *BAR_FAR_LEFT)), "the bar should reach the far left without a mask"
    assert _is_base(pixel_at(both, 1.0, *BAR_FAR_LEFT)), (
        "the circle did not clip the bar: key and mask are not intersecting"
    )
    assert _is_bar(pixel_at(both, 1.0, *BAR_CENTRE)), "the intersection removed the middle, where both agree"


def test_opacity_blends_rather_than_choosing(clips, tmp_path):
    """Half opacity is neither colour, it is between them."""
    solid = _composite(clips, tmp_path, "solid", clips["bravo"].path, x=0, y=0)
    half = _composite(clips, tmp_path, "half", clips["bravo"].path, x=0, y=0, opacity=0.5)

    base = pixel_at(solid, 1.0, 320, 180)
    blended = pixel_at(half, 1.0, 320, 180)

    assert not _is_base(blended), f"opacity 0.5 showed only the base: {blended}"
    assert blended != base, f"opacity 0.5 showed only the layer: {blended}"
    assert blended[0] > 60, f"the base's red is not coming through at all: {blended}"
    assert blended[1] > 30, f"the layer's green is not coming through at all: {blended}"


def test_opacity_one_with_no_key_is_a_genuine_no_op(clips, tmp_path):
    """The defaults must not quietly route through the alpha machinery.

    Compared pixel for pixel across a row rather than at one point: a composite
    that differed only at its edges would pass a single-point check.
    """
    from tests.fixtures import frame_row

    plain = _composite(clips, tmp_path, "plain_noop", clips["bravo"].path, x=0, y=0)
    explicit = _composite(clips, tmp_path, "explicit_noop", clips["bravo"].path, x=0, y=0, opacity=1.0)

    assert frame_row(plain, 1.0, 180) == frame_row(explicit, 1.0, 180), (
        "opacity=1 changed the picture, so it is not the no-op it claims to be"
    )


def test_an_unknown_key_is_refused_by_name(clips):
    from vid import lib

    with pytest.raises(VidError) as failure:
        lib.overlay(Plan(source=str(clips["alpha"].path)), str(clips["bravo"].path), key="greenscreen")

    message = str(failure.value)
    assert "greenscreen" in message
    assert "chromakey" in message, "the refusal does not say what IS available"


def test_an_opacity_outside_zero_to_one_is_refused(clips):
    from vid import lib

    with pytest.raises(VidError) as failure:
        lib.overlay(Plan(source=str(clips["alpha"].path)), str(clips["bravo"].path), opacity=1.5)

    assert "1.5" in str(failure.value)
