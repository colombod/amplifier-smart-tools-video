"""Manifest: the packaged SMART_TOOL.md read back as structured data."""

from pathlib import Path

import yaml

from vid.schemas import Manifest

MANIFEST_PATH = Path(__file__).parents[1] / "SMART_TOOL.md"


def load_manifest() -> Manifest:
    """Parse the frontmatter and body of the SMART_TOOL.md shipped inside the package."""
    _, frontmatter, body = MANIFEST_PATH.read_text(encoding="utf-8").split("---", 2)
    return Manifest.model_validate({**yaml.safe_load(frontmatter), "body": body.strip()})
