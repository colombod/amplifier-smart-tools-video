"""What happens to a picture-in-picture layer's own sound.

The fixtures are a fifth apart (`alpha` 440 Hz, `bravo` 660 Hz), so the two
soundtracks are separable by frequency rather than by level. That is what makes
"is the layer's tone present" answerable at all: a level check alone cannot
distinguish a layer that was mixed in from a base that got louder.

The assertion this file exists for is the one about `amix`. It defaults to
`normalize=1`, scaling every input by 1/n, so merely ADDING a layer drops the
base 3 dB. Measured before any of this was written: a 440 Hz base alone reads
max_volume -17.6 dB, and through a default `amix` with a second input, -18.5 dB.
Nothing asked for that. `test_keep_does_not_attenuate_the_base` is the guard.
"""

import pytest

from tests.fixtures import ensure_clips, ensure_silent_clip, have_ffmpeg, probe_duration, tone_strength
from vid.lib import render
from vid.plan import AudioRemove, LayerAudio, Overlay, Plan
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg is required")


@pytest.fixture(scope="module")
def clips():
    return ensure_clips()


def _with_audio(clips, tmp_path, name, **audio):
    plan = Plan(source=str(clips["alpha"].path)).with_operation(
        Overlay(
            source=str(clips["bravo"].path),
            x=20,
            y=20,
            width=320,
            height=180,
            audio=LayerAudio(**audio) if audio else None,
        )
    )
    return render(plan, str(tmp_path / f"{name}.mp4"))


def test_drop_is_the_default_and_leaves_no_trace_of_the_layer(clips, tmp_path):
    """The whole reason inclusion is stated: no accidental duplicate narration."""
    output = _with_audio(clips, tmp_path, "dropped")

    base = tone_strength(output, clips["alpha"].hz, at=1.0)
    layer = tone_strength(output, clips["bravo"].hz, at=1.0)

    assert base > layer * 4, f"the layer's tone leaked in: base={base:.4f} layer={layer:.4f}"


def test_keep_brings_both_tones_through(clips, tmp_path):
    output = _with_audio(clips, tmp_path, "kept", policy="keep")

    base = tone_strength(output, clips["alpha"].hz, at=1.0)
    layer = tone_strength(output, clips["bravo"].hz, at=1.0)

    assert base > 0.01, f"the base's tone went missing under `keep`: {base:.4f}"
    assert layer > 0.01, f"the layer's tone is not present under `keep`: {layer:.4f}"


def test_keep_does_not_attenuate_the_base(clips, tmp_path):
    """The measured `amix` trap, asserted directly.

    With `normalize=1` the base would arrive about 3 dB down purely because a
    layer exists. Compared against a base-only render rather than an absolute
    threshold, so the test is about the CHANGE and not about a level that could
    drift for unrelated reasons.
    """
    dropped = _with_audio(clips, tmp_path, "level_dropped")
    kept = _with_audio(clips, tmp_path, "level_kept", policy="keep")

    without_layer = tone_strength(dropped, clips["alpha"].hz, at=1.0)
    with_layer = tone_strength(kept, clips["alpha"].hz, at=1.0)

    assert with_layer == pytest.approx(without_layer, rel=0.25), (
        f"adding a layer changed the base's own level: {without_layer:.4f} -> {with_layer:.4f}. "
        "That is amix normalize=1 scaling every input by 1/n."
    )


def test_only_replaces_the_bases_sound(clips, tmp_path):
    output = _with_audio(clips, tmp_path, "only", policy="only")

    base = tone_strength(output, clips["alpha"].hz, at=1.0)
    layer = tone_strength(output, clips["bravo"].hz, at=1.0)

    assert layer > base * 4, f"`only` did not replace the base: base={base:.4f} layer={layer:.4f}"


def test_layer_gain_changes_the_balance_in_the_stated_direction(clips, tmp_path):
    """A stated gain has a measurable, directional effect."""
    level = _with_audio(clips, tmp_path, "gain_flat", policy="keep")
    quiet = _with_audio(clips, tmp_path, "gain_down", policy="keep", gain_db=-12.0)

    at_zero = tone_strength(level, clips["bravo"].hz, at=1.0)
    at_minus_twelve = tone_strength(quiet, clips["bravo"].hz, at=1.0)

    assert at_minus_twelve < at_zero * 0.6, (
        f"-12 dB did not measurably quieten the layer: {at_zero:.4f} -> {at_minus_twelve:.4f}"
    )


def test_the_layers_sound_starts_when_the_layer_does(clips, tmp_path):
    """Placement, not just inclusion: absent before the layer appears."""
    plan = Plan(source=str(clips["alpha"].path)).with_operation(
        Overlay(
            source=str(clips["bravo"].path),
            x=20,
            y=20,
            width=320,
            height=180,
            start=1.5,
            audio=LayerAudio(policy="keep"),
        )
    )
    output = render(plan, str(tmp_path / "placed_sound.mp4"))

    before = tone_strength(output, clips["bravo"].hz, at=0.5)
    after = tone_strength(output, clips["bravo"].hz, at=2.2)

    assert after > before * 4, f"the layer's sound did not wait for the layer: {before:.4f} -> {after:.4f}"


def test_every_policy_leaves_the_duration_alone(clips, tmp_path):
    for policy in ("drop", "keep", "only"):
        output = _with_audio(clips, tmp_path, f"duration_{policy}", policy=policy)
        assert probe_duration(output) == pytest.approx(clips["alpha"].seconds, abs=0.15), (
            f"policy `{policy}` changed the edit's duration"
        )


def test_ducking_renders_and_keeps_both_tones(clips, tmp_path):
    """sidechaincompress must survive the split, and not eat the mix.

    The dip itself is a level change over time that `tone_strength` at one
    instant cannot cleanly separate from the tone's own envelope, so this
    asserts the weaker, honest claim: it renders, the timing holds, and neither
    soundtrack disappeared.
    """
    output = _with_audio(clips, tmp_path, "ducked", policy="keep", duck=True)

    assert probe_duration(output) == pytest.approx(clips["alpha"].seconds, abs=0.15)
    assert tone_strength(output, clips["alpha"].hz, at=1.0) > 0.005, "ducking removed the base entirely"
    assert tone_strength(output, clips["bravo"].hz, at=1.0) > 0.005, "ducking removed the layer entirely"


def test_keeping_a_silent_layers_audio_is_refused_by_name(clips, tmp_path):
    """There is nothing to keep, and ffmpeg's own error names neither file."""
    from vid import lib

    silent = ensure_silent_clip()
    plan = Plan(source=str(clips["alpha"].path)).with_operation(
        Overlay(source=str(silent.path), x=20, y=20, width=320, height=180, audio=LayerAudio(policy="keep"))
    )

    with pytest.raises(VidError) as failure:
        lib.render(plan, str(tmp_path / "silent_layer.mp4"))

    assert "silent.mp4" in str(failure.value)


def test_a_base_with_no_sound_never_writes_none_into_the_graph(clips, tmp_path):
    """`audio remove` then a `keep` overlay: the layer simply becomes the track."""
    plan = (
        Plan(source=str(clips["alpha"].path))
        .with_operation(AudioRemove())
        .with_operation(
            Overlay(source=str(clips["bravo"].path), x=20, y=20, width=320, height=180, audio=LayerAudio(policy="keep"))
        )
    )
    output = render(plan, str(tmp_path / "removed_base.mp4"))

    assert tone_strength(output, clips["bravo"].hz, at=1.0) > 0.01, "the layer's sound did not survive"
    assert probe_duration(output) == pytest.approx(clips["alpha"].seconds, abs=0.15)
