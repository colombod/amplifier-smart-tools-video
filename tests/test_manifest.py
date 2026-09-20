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
