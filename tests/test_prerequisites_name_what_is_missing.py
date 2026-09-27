"""A missing prerequisite must name the binary that is ACTUALLY absent.

Deviation `prerequisite-failures-match-manifest`. `check-spec-adherence`
reported it at `color.py` on run 6 and at `index.py` on run 7 -- one instance
per run, each time citing the previously-fixed one as the model to copy.

FOUR SITES GUARDED ON THE SAME TWO BINARIES, and each wrote its own sentence,
so each was free to be wrong in its own way. Three of the four were:

    index.py      said "ffmpeg is not on PATH" whichever was missing
    lib.verify    said "it needs both", naming neither as the absent one
    lib.index     same
    color.py      correct, and only because it had been reported

Fixing the reported site a third time would have left `verify` and `lib.index`
wrong and waiting for run 8. The rule now lives once, in
`probe.require_ffmpeg_tools`, and this file tests the RULE and then pins every
call site to it -- so a fifth site cannot reintroduce the defect by writing its
own sentence.

Reproduced before the fix, with ffmpeg alone on PATH:

    ffmpeg present: True | ffprobe present: False
    MESSAGE SAYS: "ffmpeg is not on PATH, and recolor needs it to sample colour..."

DELIBERATELY NOT SKIPPED when ffmpeg is absent. A test about missing binaries
that skips itself when binaries are missing is the one arrangement guaranteed
never to run where it matters.
"""

from __future__ import annotations

import pytest

from vid import probe
from vid.core.manifest import manifest_install
from vid.schemas import VidError


@pytest.mark.parametrize(
    ("ffmpeg", "ffprobe", "named", "not_named"),
    [
        (True, False, "ffprobe", "ffmpeg is not"),
        (False, True, "ffmpeg", "ffprobe is not"),
    ],
    ids=["ffmpeg present, ffprobe missing", "ffprobe present, ffmpeg missing"],
)
def test_the_absent_binary_is_the_one_named(monkeypatch, ffmpeg, ffprobe, named, not_named) -> None:
    monkeypatch.setattr(probe, "have_ffmpeg", lambda: ffmpeg)
    monkeypatch.setattr(probe, "have_ffprobe", lambda: ffprobe)

    with pytest.raises(VidError) as failure:
        probe.require_ffmpeg_tools("indexing needs them")

    message = str(failure.value)
    assert f"{named} is not on PATH" in message, f"the failure does not name the absent binary: {message!r}"
    assert not_named not in message, f"the failure blames a binary that is present: {message!r}"


def test_both_missing_names_both(monkeypatch) -> None:
    monkeypatch.setattr(probe, "have_ffmpeg", lambda: False)
    monkeypatch.setattr(probe, "have_ffprobe", lambda: False)

    with pytest.raises(VidError) as failure:
        probe.require_ffmpeg_tools("indexing needs them")

    assert "ffmpeg and ffprobe are not on PATH" in str(failure.value)


def test_both_present_is_not_a_failure(monkeypatch) -> None:
    """The guard must not have started refusing a working installation."""
    monkeypatch.setattr(probe, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(probe, "have_ffprobe", lambda: True)

    probe.require_ffmpeg_tools("indexing needs them")


def test_the_remedy_comes_from_the_manifest(monkeypatch) -> None:
    """The spec requires the failure and the manifest to agree.

    The install reference is read from `vid.core.manifest` rather than a
    second hand-copied string, so the two cannot drift.
    """
    monkeypatch.setattr(probe, "have_ffmpeg", lambda: False)
    monkeypatch.setattr(probe, "have_ffprobe", lambda: False)

    with pytest.raises(VidError) as failure:
        probe.require_ffmpeg_tools("indexing needs them")

    assert manifest_install("ffmpeg") in str(failure.value)


def test_the_callers_explanation_survives(monkeypatch) -> None:
    """Each site keeps its own half of the sentence; only the naming is shared."""
    monkeypatch.setattr(probe, "have_ffmpeg", lambda: False)
    monkeypatch.setattr(probe, "have_ffprobe", lambda: True)

    with pytest.raises(VidError) as failure:
        probe.require_ffmpeg_tools("recolor needs them to sample colour from a frame")

    assert "recolor needs them to sample colour from a frame" in str(failure.value)


# ---------------------------------------------------------------------------
# Every call site, pinned to the shared rule. This is what stops the class
# regrowing: a site that goes back to writing its own sentence fails here.
# ---------------------------------------------------------------------------


def _sites():
    """(label, callable) for each guard, built lazily so imports stay local."""
    from vid import color, index, lib

    return [
        ("color._require_ffmpeg_tools", color._require_ffmpeg_tools),
        ("index._require_ffmpeg_tools", index._require_ffmpeg_tools),
        ("lib.verify", lambda: lib.verify("x.mp4")),
        ("lib.index", lambda: lib.index("x.mp4")),
    ]


@pytest.mark.parametrize("label", [label for label, _ in _sites()])
def test_every_call_site_names_the_absent_binary(monkeypatch, label) -> None:
    """ffmpeg present, ffprobe missing -- every site must blame ffprobe."""
    monkeypatch.setattr(probe, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(probe, "have_ffprobe", lambda: False)

    call = dict(_sites())[label]
    with pytest.raises(VidError) as failure:
        call()

    message = str(failure.value)
    assert "ffprobe is not on PATH" in message, f"{label} does not name the absent binary: {message!r}"
    assert "ffmpeg is not" not in message, f"{label} blames ffmpeg, which is present: {message!r}"


# ---------------------------------------------------------------------------
# The class, enumerated. Fixing reported members one at a time is what kept
# this alive for four rounds of check-spec-adherence: run 6 named color.py,
# run 7 named index.py, run 9 named three more in probe.py. Each round the
# checker cited the previously-fixed site as the model to copy.
#
# Every refusal about a missing binary now goes through one of three shared
# helpers, all of which carry the manifest's install reference. This walks the
# source and fails on a site that raises its own.
# ---------------------------------------------------------------------------

import ast  # noqa: E402
from pathlib import Path  # noqa: E402

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "vid"

SHARED_HELPERS = frozenset({"require_ffmpeg", "require_ffprobe", "require_ffmpeg_tools", "_refuse_missing"})


def _hand_written_binary_refusals() -> list[str]:
    """`if not have_ffX(): raise ...` written out longhand, anywhere in src."""
    found = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            probes = {
                n.func.id for n in ast.walk(node.test) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            if not probes & {"have_ffmpeg", "have_ffprobe"}:
                continue
            raises = [n for n in ast.walk(node) if isinstance(n, ast.Raise)]
            if raises:
                found.append(f"{path.relative_to(SOURCE_ROOT.parents[1])}:{node.lineno}")
    return found


def test_no_site_writes_its_own_missing_binary_refusal() -> None:
    """The shared helpers are the only place that sentence is composed."""
    longhand = [site for site in _hand_written_binary_refusals() if "probe.py" not in site]

    assert not longhand, (
        "these refuse a missing ffmpeg/ffprobe with their own message instead of calling "
        "probe.require_ffmpeg / require_ffprobe / require_ffmpeg_tools, so they can name the "
        "wrong binary or omit the manifest's install reference: " + ", ".join(longhand)
    )


def test_every_shared_helper_carries_the_manifest_install_reference(monkeypatch) -> None:
    """Run 9's finding: four refusals said only "Run `vid check`"."""
    monkeypatch.setattr(probe, "have_ffmpeg", lambda: False)
    monkeypatch.setattr(probe, "have_ffprobe", lambda: False)
    reference = manifest_install("ffmpeg")

    for helper in (probe.require_ffmpeg, probe.require_ffprobe, probe.require_ffmpeg_tools):
        with pytest.raises(VidError) as failure:
            helper("something needs it")
        assert reference in str(failure.value), f"{helper.__name__} omits the manifest install reference"


def test_the_single_binary_helpers_name_only_the_one_they_check(monkeypatch) -> None:
    monkeypatch.setattr(probe, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(probe, "have_ffprobe", lambda: False)

    with pytest.raises(VidError) as failure:
        probe.require_ffprobe("a transition needs to know how long the clips are")
    assert "ffprobe is not on PATH" in str(failure.value)
    assert "a transition needs to know how long the clips are" in str(failure.value)

    probe.require_ffmpeg("this must not raise -- ffmpeg is present")


def test_the_detector_can_see_a_hand_written_refusal(tmp_path) -> None:
    """The enumeration must FAIL on known-bad source, or it proves nothing."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "def f():\n    if not have_ffprobe():\n        raise VidError('ffmpeg is not on PATH. Run `vid check`.')\n"
    )
    tree = ast.parse(bad.read_text())
    offending = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.If)
        and {c.func.id for c in ast.walk(n.test) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        & {"have_ffmpeg", "have_ffprobe"}
        and any(isinstance(r, ast.Raise) for r in ast.walk(n))
    ]
    assert offending, "the detector cannot see a hand-written refusal it is meant to catch"
