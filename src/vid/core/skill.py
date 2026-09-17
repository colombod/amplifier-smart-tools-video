"""Skill: what the tool tells an agent that has decided to drive it."""

from importlib.metadata import metadata
from pathlib import Path

from vid.core.manifest import MANIFEST_PATH, load_manifest
from vid.schemas import Capability, VidError

DISTRIBUTION = "vid"

# One table drives the skill's capability list, so it cannot drift from what the CLI exposes.
# Every capability added to the library gets a row here.
CAPABILITIES = (
    Capability("trim", "Keep a time range, discard the rest.", model_backed=False),
    Capability("cut", "Remove a time range, keeping what surrounds it.", model_backed=False),
    Capability("retime", "Change speed, constantly or along a curve.", model_backed=False),
    Capability("zoom", "Animated zoom (Ken Burns), with the jitter fix applied.", model_backed=False),
    Capability("stitch", "Join clips, with or without a transition.", model_backed=False),
    Capability("caption", "Burn subtitles into the picture.", model_backed=False),
    Capability("plan", "Show the edit as JSON, without performing it.", model_backed=False),
    Capability("render", "Compile the plan and encode, once. The ONLY verb that touches pixels.", model_backed=False),
    Capability("check", "What this installation can actually do, and what would unlock the rest.", model_backed=False),
    Capability("manifest", "Print the tool's manifest as JSON.", model_backed=False),
)

# Paths relative to the skill directory. Both ship inside the package, so both resolve after installation.
SKILL_RESOURCES = ("SMART_TOOL.md", "lib.py")


def skill_directory() -> Path:
    """The installed package root, resolved at runtime, where the tool's own files live."""
    return MANIFEST_PATH.parent.resolve()


def repository_url() -> str | None:
    """The tool's canonical source, from the package metadata, or None when the package declares none."""
    for entry in metadata(DISTRIBUTION).get_all("Project-URL") or []:
        label, _, url = str(entry).partition(",")
        if label.strip().lower() == "repository":
            return url.strip()
    return None


def skill_resources() -> list[str]:
    """The files the skill lists, as paths relative to the skill directory."""
    root = skill_directory()
    missing = [path for path in SKILL_RESOURCES if not (root / path).is_file()]
    if missing:
        raise VidError(
            f"The skill names files that are not in the installed package: {', '.join(missing)}. "
            f"Ship them under {root} or drop them from SKILL_RESOURCES."
        )
    return list(SKILL_RESOURCES)


def skill() -> str:
    """Compose the tool's skill: the manifest body, the capability list, and where the tool's files are."""
    manifest = load_manifest()
    repository = repository_url()
    lines = [
        f'<skill_content name="{manifest.name}">',
        f"Skill directory: {skill_directory()}",
    ]
    # A caller that can run the tool cannot always read its files, so the canonical source stands in for them.
    if repository:
        lines.append(f"Repository: {repository}")
    lines += [
        "Relative paths in this skill are relative to the skill directory.",
        "",
        f"# {manifest.name}",
        "",
        manifest.body,
        "",
        "## Capabilities",
        "",
    ]
    for capability in CAPABILITIES:
        kind = "model-backed" if capability.model_backed else "deterministic"
        lines.append(
            f"- `{capability.name}` [{kind}] -- {capability.summary} "
            f"Arguments, result, and exit codes: `{manifest.name} {capability.name} --help`."
        )
    lines += ["", "<skill_resources>"]
    lines += [f"  <file>{path}</file>" for path in skill_resources()]
    lines += ["</skill_resources>", "</skill_content>"]
    return "\n".join(lines)
