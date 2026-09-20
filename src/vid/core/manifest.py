"""Manifest: the packaged SMART_TOOL.md read back as structured data."""

from pathlib import Path

import yaml

from vid.schemas import Manifest

MANIFEST_PATH = Path(__file__).parents[1] / "SMART_TOOL.md"


def load_manifest() -> Manifest:
    """Parse the frontmatter and body of the SMART_TOOL.md shipped inside the package."""
    _, frontmatter, body = MANIFEST_PATH.read_text(encoding="utf-8").split("---", 2)
    return Manifest.model_validate({**yaml.safe_load(frontmatter), "body": body.strip()})


def manifest_install(name: str) -> str:
    """The shipped manifest's own install reference for prerequisite `name`.

    Read out of `SMART_TOOL.md` rather than hand-copied, so a preflight
    failure can never cite an install reference that disagrees with the one
    the manifest already declares. Shared by every preflight that needs to
    name a remedy: `vid.intelligence.copilot` (for `gh` and the Copilot
    subscription) and `vid.color` (for ffmpeg, ahead of recolor's sampling).
    """
    for requirement in load_manifest().requires:
        if requirement.name == name:
            return requirement.install
    return ""
