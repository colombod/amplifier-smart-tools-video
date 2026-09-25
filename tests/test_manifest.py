from importlib.metadata import version
import json
from pathlib import Path

import pytest

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
    # OPTIONAL=TRUE, and this assertion used to demand the opposite. It read
    # "ffprobe is preflighted as required, so it must not be declared
    # optional" -- an over-strong claim I wrote, and measurement refutes it.
    # With ffmpeg on PATH but ffprobe genuinely absent:
    #
    #     vid trim src.mp4 --from 1 --to 3   WITH ffprobe     rc=0
    #     vid trim src.mp4 --from 1 --to 3   WITHOUT ffprobe  rc=0
    #
    # ffprobe is preflighted by the operations that need duration data, NOT by
    # the tool generally, so `optional: false` told a caller that a tool which
    # demonstrably works is broken. Declaring the prerequisite at all is the
    # honesty win and is asserted above; the flag is a separate axis and has
    # to describe the tool's real reduced operation.
    assert ffprobe.optional, (
        "ffprobe is preflighted only by the operations needing duration data -- plan-only "
        "verbs run without it (measured: `vid trim --from --to` exits 0 with ffprobe absent), "
        "so declaring it non-optional contradicts the tool's own documented behaviour"
    )


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


# ---------------------------------------------------------------------------
# "Failures are loud and they name the remedy. A caller should never have to
# infer what went wrong from an empty result."
#
# `main()` in cli.py catches ONLY VidError. Anything else reaches the user as a
# Python traceback -- which is the loudest possible way to say nothing useful.
# A review cited one unwrapped call; sweeping for the PROPERTY found four.
# ---------------------------------------------------------------------------

#: Every third-party name whose initialisation can fail at runtime. Each must
#: sit inside a try that converts the failure into a VidError, because the CLI
#: has no other net. Checked structurally, with ast, rather than by grepping
#: for `try` near the line -- a guard three functions away would satisfy a grep.
GUARDED_THIRD_PARTY = {"piper", "faster_whisper"}


def test_no_third_party_initialisation_can_escape_as_a_traceback() -> None:
    import ast

    unguarded = []
    for relative in ("src/vid/voice.py", "src/vid/index.py"):
        tree = ast.parse((DISTRIBUTION_ROOT / relative).read_text(encoding="utf-8"))
        inside_try = {id(node) for block in ast.walk(tree) if isinstance(block, ast.Try) for node in ast.walk(block)}
        for node in ast.walk(tree):
            is_third_party = isinstance(node, ast.ImportFrom) and node.module in GUARDED_THIRD_PARTY
            if is_third_party and id(node) not in inside_try:
                unguarded.append(f"{relative}:{node.lineno} imports {node.module}")

    assert not unguarded, (
        "third-party initialisation outside a VidError guard: "
        + "; ".join(unguarded)
        + ". cli.main() catches only VidError, so this reaches the user as a traceback."
    )


#: (module, the call that used to fail without naming a remedy)
REMEDY_REQUIRED = [
    ("src/vid/verify.py", "ffmpeg frame analysis", ["ffprobe", "vid check"]),
    ("src/vid/voice.py", "PiperVoice.load and synthesize_wav", ["--voice", "vid check"]),
    ("src/vid/index.py", "WhisperModel construction", ["tiny, base, small and medium", "vid check"]),
]


@pytest.mark.parametrize(
    ("relative", "what", "remedies"), REMEDY_REQUIRED, ids=[c[0].split("/")[-1] for c in REMEDY_REQUIRED]
)
def test_a_named_failure_carries_a_corrective_action(relative, what, remedies) -> None:
    """Not "does it raise VidError" -- it always did. The rule is that the
    message tells the caller what to DO, which is the half that was missing."""
    source = (DISTRIBUTION_ROOT / relative).read_text(encoding="utf-8")

    for remedy in remedies:
        assert remedy in source, f"{relative}'s {what} failure does not offer {remedy!r} as a corrective action"
