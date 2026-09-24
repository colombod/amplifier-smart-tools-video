"""The fixtures must be tellable apart, and this asserts it about the fixtures.

Every measured overlay test classifies a decoded pixel as "base" or "layer".
That classification is load-bearing: if a colour in one fixture can satisfy the
other's predicate, the test reports a confident result about nothing.

This is not hypothetical. Three separate probes in this feature's history
measured somewhere the difference could not appear:

  * `testsrc2` was used as the layer with an "is it red" check to tell layer
    from base -- and testsrc2 contains its own red bars, so the check reported
    "base" over pixels the layer had in fact drawn.
  * ellipse and rounded_rect were compared by their horizontal and vertical
    extent, which is 640x360 for both: true of each, distinguishing neither.
  * a colour key was tested against a mask using a 200x200 subject that sat
    entirely inside the circle, so keyed-and-masked was pixel-identical to
    keyed-alone everywhere sampled.

So the predicates are tested here against the actual fixture content, rather
than trusted because the ffmpeg command that built them said "red".
"""

from itertools import pairwise

import pytest

from tests.fixtures import ensure_clips, ensure_keyable_clip, frame_row, have_ffmpeg

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg is required")

#: The predicates the overlay tests actually use, restated here so a drift in
#: one is caught rather than silently agreed with.
IS_BASE = lambda c: c[0] > 150 and c[1] < 110  # noqa: E731
IS_LAYER = lambda c: c[1] > 80 and c[0] < 150  # noqa: E731
IS_BAR = lambda c: c[2] > 120 and c[0] < 120  # noqa: E731


@pytest.fixture(scope="module")
def clips():
    return ensure_clips()


def test_no_pixel_of_the_base_can_be_read_as_the_layer(clips):
    """`alpha` is the base in every overlay test. None of it may look like `bravo`."""
    row = frame_row(clips["alpha"].path, 1.0, 180)

    misread = [c for c in row if IS_LAYER(c)]
    assert not misread, f"{len(misread)} pixels of the base satisfy the layer predicate, e.g. {misread[:3]}"
    assert all(IS_BASE(c) for c in row), "the base does not satisfy its own predicate"


def test_no_pixel_of_the_layer_can_be_read_as_the_base(clips):
    row = frame_row(clips["bravo"].path, 1.0, 180)

    misread = [c for c in row if IS_BASE(c)]
    assert not misread, f"{len(misread)} pixels of the layer satisfy the base predicate, e.g. {misread[:3]}"
    assert all(IS_LAYER(c) for c in row), "the layer does not satisfy its own predicate"


def test_the_keyable_fixture_separates_its_field_from_its_subject():
    """The keyed colour and the kept subject must not overlap either.

    Sampled on two different rows: the bar's row must be entirely subject, and
    a row above it entirely field. A fixture whose subject bled into the field
    would make every keying assertion meaningless.
    """
    keyable = ensure_keyable_clip()
    bar_row = frame_row(keyable.path, 1.0, 180)
    field_row = frame_row(keyable.path, 1.0, 40)

    assert all(IS_BAR(c) for c in bar_row), "the bar row is not uniformly the subject"
    assert not any(IS_BAR(c) for c in field_row), "the green field contains pixels that read as the subject"


def test_the_keyable_subject_spans_the_full_width():
    """The geometry the key-plus-mask test depends on.

    If the bar ever stopped short of the frame's edge, the discriminating point
    at x=30 would fall outside the subject and the intersection test would pass
    for the wrong reason.
    """
    keyable = ensure_keyable_clip()
    row = frame_row(keyable.path, 1.0, 180)

    assert IS_BAR(row[5]), "the bar does not reach the left edge"
    assert IS_BAR(row[-5]), "the bar does not reach the right edge"


def test_the_three_clip_tones_are_separable_by_frequency(clips):
    """Audio's equivalent: a level check cannot tell a mix from a louder base."""
    frequencies = {clip.hz for clip in clips.values()}

    assert len(frequencies) == len(clips), f"two fixtures share a tone: {[c.hz for c in clips.values()]}"
    ordered = sorted(frequencies)
    for lower, higher in pairwise(ordered):
        assert higher / lower > 1.2, f"{lower} Hz and {higher} Hz are too close to separate reliably"
