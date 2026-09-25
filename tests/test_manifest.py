from importlib.metadata import version
import json
from pathlib import Path

from vid.core.manifest import MANIFEST_PATH
from vid.lib import load_manifest

DISTRIBUTION_ROOT = Path(__file__).parents[1]


def test_manifest_matches_the_descriptor_and_package() -> None:
    manifest = load_manifest()
    descriptor = json.loads((DISTRIBUTION_ROOT / "smart-tool.json").read_text(encoding="utf-8"))

    assert DISTRIBUTION_ROOT / descriptor["manifest"] == MANIFEST_PATH
    assert manifest.name == descriptor["cli_argv"][0]
    assert manifest.version == version("vid")


def test_manifest_install_reads_the_reference_for_a_declared_requirement() -> None:
    """Shared by every preflight that needs to name a remedy (`vid.color`,
    `vid.intelligence.copilot`) -- one canonical home so none of them can
    drift from what the manifest actually declares."""
    from vid.core.manifest import manifest_install

    assert manifest_install("ffmpeg") == "https://ffmpeg.org/download.html"


def test_manifest_install_returns_empty_for_a_requirement_the_manifest_does_not_declare() -> None:
    from vid.core.manifest import manifest_install

    assert manifest_install("not-a-real-requirement") == ""


# ---------------------------------------------------------------------------
# The spec's rule: "A missing prerequisite fails immediately, naming what is
# absent and how to install it. The manifest already declares these, so the
# failure and the manifest must agree."
#
# `ffprobe` broke that agreement in the one direction that is hardest to spot:
# the CODE checked for it in five separate places and refused by name, while
# the MANIFEST declared only ffmpeg. An agent reading the manifest to decide
# whether it could run `vid` got an incomplete answer, and then met a refusal
# naming something the manifest never admitted existed.
# ---------------------------------------------------------------------------

#: Every site that independently preflights ffprobe, verified by reading them.
#: ffprobe ships with ffmpeg in every mainstream distribution -- but a minimal
#: container image that installs an `ffmpeg` binary alone reaches these.
FFPROBE_PREFLIGHTS = [
    ("src/vid/probe.py", "duration() and the stream reads"),
    ("src/vid/lib.py", "stitch, which must know how long the clips are"),
    ("src/vid/index.py", "index"),
    ("src/vid/color.py", "recolor"),
]


def test_the_manifest_declares_the_prerequisite_the_code_checks_for() -> None:
    manifest = load_manifest()
    names = {requirement.name for requirement in manifest.requires}

    assert "ffprobe" in names, (
        "the code preflights ffprobe in four modules and refuses by name when it is absent, "
        f"but the manifest declares only {sorted(names)}"
    )


def test_the_declared_prerequisite_carries_a_purpose_and_an_install_reference() -> None:
    """A `requires` entry with no remedy is a name, not a prerequisite."""
    from vid.core.manifest import manifest_install

    manifest = load_manifest()
    ffprobe = next(r for r in manifest.requires if r.name == "ffprobe")

    assert ffprobe.purpose.strip(), "ffprobe is declared with no purpose"
    assert manifest_install("ffprobe"), "ffprobe is declared with no install reference"
    assert not ffprobe.optional, "ffprobe is preflighted as required, so it must not be declared optional"


def test_every_ffprobe_refusal_names_what_the_manifest_declares() -> None:
    """The agreement, checked at every site rather than the one a review cited.

    Reading source rather than forcing five separate PATH-stripped runs: the
    property is that each refusal NAMES the prerequisite, which is a property
    of the message, and this catches a fifth site being added without one.
    """
    for relative, what in FFPROBE_PREFLIGHTS:
        source = (DISTRIBUTION_ROOT / relative).read_text(encoding="utf-8")
        assert "have_ffprobe()" in source, f"{relative} no longer preflights ffprobe; this list is stale"
        assert "ffprobe" in source, f"{relative} preflights ffprobe for {what} but never names it in a refusal"


def test_the_description_stays_under_the_agent_skills_cap() -> None:
    """The 1024-char cap is not enforced by anything at install time -- a host
    degrades silently past it. Adding a `requires` entry does not touch the
    description, but prose edits nearby have breached it before."""
    manifest = load_manifest()

    assert len(manifest.description) <= 1024, (
        f"description is {len(manifest.description)} chars, over the 1024 Agent Skills cap"
    )
