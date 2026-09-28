"""#27: a silent clip in a join must not change the edit's audio format.

WHAT WAS WRONG, AND IT COST THE CALLER 3 dB. Synthesised silence was generated
at a fixed 48 kHz stereo, and a real track beside it was conformed UP to meet
that. So adding ONE silent clip to a stitch changed the format of the WHOLE
output -- and because mono to stereo spreads one channel's energy over two, the
caller's real audio got quieter. Measured end to end through the CLI on a
44.1 kHz mono source, before the fix:

    stitch, every clip has real audio  ->  44100/1, true peak -17.2 dBFS
    stitch, ONE silent clip            ->  48000/2, true peak -20.2 dBFS
    that same output remixed to mono   ->  48000/1, true peak -17.2 dBFS

The third row is the control: the 3 dB is the channel split, not lost signal.
And the first row is the argument -- a stitch whose clips ALL carry audio
already preserved 44100/1, so following the edit's own format makes the silence
path CONSISTENT with what the rest of stitch already did.

WHY THE SUITE COULD NOT SEE THIS. Every other audio fixture in this repo is
built at 48000 Hz. "Conform to a fixed 48 kHz stereo" and "keep the source's
own format" produce identical bytes for all of them, so the pin was untestable
by construction -- removing it broke nothing, across 694 tests. These tests use
`ensure_44k_mono_clip`, which exists precisely to differ from the constant.

ON #22's ORIGINAL MEASUREMENT, which chose the pin and was real. Its reporter
wrote: "The silent track must be stereo 48 kHz. A mono or slightly long AAC bed
made the picture drift by up to 5 frames and pushed the final mix's true peak
up." That evidence bundled TWO variables, and each got its own fix. Once the
silence length came from the probed duration, the drift half stopped
reproducing: patching the constants to 44100/mono and re-rendering measured
0.00 frames of drift and an identical 45-frame output. The true-peak half is
inverted from the claim -- mono PRESERVES the source's -17.2 dBFS; the stereo
pin is what moved it.

A measured decision stays true only while the conditions it was measured under
hold. A sibling fix retired this one, and nothing re-checked.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from tests.fixtures import (
    audio_stream_format,
    ensure_44k_mono_clip,
    ensure_silent_640_clip,
    have_ffmpeg,
    true_peak_dbfs,
)

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="#27 is a render-path behaviour")

REPO = Path(__file__).resolve().parents[1]


def _render(plan: dict, out: Path) -> None:
    result = subprocess.run(
        [str(REPO / ".venv" / "bin" / "vid"), "render", str(out)],
        input=json.dumps(plan),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _render_with_the_old_pin(plan: dict, out: Path) -> None:
    """Render the same plan with the format probe suppressed.

    That is precisely the pre-fix behaviour: with nothing probed the compiler
    falls back to its 48 kHz stereo constants, so this drives the OLD code path
    through the SAME binary and fixture as the new one.
    """
    script = "\n".join(
        [
            "import json, sys",
            "import vid.probe as probe",
            "probe.audio_format = lambda path: None",
            "from vid.lib import render",
            "from vid.plan import Plan",
            "render(Plan.model_validate(json.loads(sys.argv[1])), sys.argv[2])",
        ]
    )
    result = subprocess.run(
        [str(REPO / ".venv" / "bin" / "python"), "-c", script, json.dumps(plan), str(out)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_a_silent_clip_does_not_change_the_edits_sample_rate_or_channels(tmp_path: Path) -> None:
    """The defect itself, at the layer that shipped it.

    Driven through the real CLI rather than the compiler, because the format
    the caller ends up with is a property of the RENDERED FILE. A compile-level
    assertion on the filter string would have passed against a graph that still
    produced the wrong output -- that exact mistake shipped on this branch once
    already, in the trim past-the-end guard.
    """
    source = ensure_44k_mono_clip()
    silent = ensure_silent_640_clip()
    out = tmp_path / "out.mp4"

    _render(
        {
            "plan_format": 1,
            "source": str(source.path),
            "operations": [{"op": "stitch", "sources": [str(silent.path)]}],
        },
        out,
    )

    assert audio_stream_format(source.path) == (44100, 1), "fixture drifted; the test can no longer see the defect"
    assert audio_stream_format(out) == (44100, 1), (
        "a silent clip changed the edit's audio format. Before the fix this was (48000, 2): "
        "the synthesised silence was pinned to 48 kHz stereo and the real track was conformed up to meet it."
    )


def test_the_old_pin_changed_the_level_and_the_new_path_does_not(tmp_path: Path) -> None:
    """The level claim, measured A/B INSIDE ONE ENVIRONMENT.

    An earlier version of this test compared the output's absolute true peak
    against the source's. CI refuted it: on ffmpeg 6.1.1 that comparison moved
    by 3.1 dB even for a join of REAL audio with no silence in it -- a path
    this change never touches. So the instrument was measuring the ffmpeg
    version and the concat join, not the defect.

    ebur128's true peak is oversampled and version-sensitive, so an absolute
    number is not portable. A DIFFERENCE between two renders made by the same
    binary from the same fixture is. This renders the identical plan twice --
    once with the probe suppressed, which is exactly the old pinned behaviour,
    and once normally -- and asserts the pinned one moves the level while the
    probed one does not.

    That is also a stronger claim than the original: it does not merely say the
    new path is fine, it demonstrates the old path was not.
    """
    source = ensure_44k_mono_clip()
    silent = ensure_silent_640_clip()
    plan = {
        "plan_format": 1,
        "source": str(source.path),
        "operations": [{"op": "stitch", "sources": [str(silent.path)]}],
    }

    probed_out = tmp_path / "probed.mp4"
    _render(plan, probed_out)

    pinned_out = tmp_path / "pinned.mp4"
    _render_with_the_old_pin(plan, pinned_out)

    probed_peak = true_peak_dbfs(probed_out)
    pinned_peak = true_peak_dbfs(pinned_out)

    assert audio_stream_format(pinned_out) == (48000, 2), (
        "suppressing the probe should reproduce the old 48 kHz stereo pin; if it does not, "
        "this test is no longer comparing the two behaviours it claims to compare"
    )
    assert audio_stream_format(probed_out) == (44100, 1)

    # ONLY THE TWO OUTPUTS ARE COMPARED, AND THAT IS THE WHOLE POINT.
    # Comparing either one against the SOURCE is what CI refuted: on its
    # ffmpeg that comparison moved 3.1 dB for a join of real audio with no
    # silence in it, a path this change never touches. The concat join itself
    # and the ebur128 version both shift an absolute reading.
    #
    # These two renders share the same binary, the same fixture and the same
    # join. The ONLY difference between them is the format path, so their
    # difference isolates it and the shared artifacts cancel.
    assert probed_peak - pinned_peak > 1.0, (
        f"the old pin made the caller's audio quieter by spreading one channel's energy over "
        f"two; measured -3.0 dB by hand. Here the pinned render is {pinned_peak} dBFS and the "
        f"probed one {probed_peak} dBFS, a difference of {probed_peak - pinned_peak:.1f} dB. "
        "If this no longer holds, the 3 dB claim in this file's docstring needs re-measuring, "
        "not relaxing."
    )


def test_a_join_of_real_audio_keeps_the_source_format(tmp_path: Path) -> None:
    """The control, and the reason the fix is a restoration rather than a new policy.

    A stitch whose clips all carry audio ALREADY preserved the source format.
    Asserts FORMAT only: the absolute true peak of a concat is not stable
    across ffmpeg versions, which is what CI proved when an earlier version of
    this test asserted it here.
    """
    source = ensure_44k_mono_clip()
    out = tmp_path / "out.mp4"

    _render(
        {
            "plan_format": 1,
            "source": str(source.path),
            "operations": [{"op": "stitch", "sources": [str(source.path)]}],
        },
        out,
    )

    assert audio_stream_format(out) == (44100, 1)


def test_print_command_still_compiles_without_probing_anything() -> None:
    """The fallback rung, and the contract it protects.

    `--print-command` is a PURE compile: it must work on a machine with no
    ffmpeg and no media on disk, so it cannot ffprobe a format. The constants
    remain for exactly this case. Adding `Trim` to the duration probe broke
    this same contract earlier on this branch, which is why it is asserted here
    rather than assumed.
    """
    plan = json.dumps(
        {
            "plan_format": 1,
            "source": "talk.mp4",
            "operations": [{"op": "stitch", "sources": ["second.mp4"]}],
        }
    )
    result = subprocess.run(
        [str(REPO / ".venv" / "bin" / "vid"), "render", "out.mp4", "--print-command"],
        input=plan,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("ffmpeg ")
    assert not (REPO / "out.mp4").exists()
