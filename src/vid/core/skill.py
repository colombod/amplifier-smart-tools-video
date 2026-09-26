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
    Capability("overlay", "Lay another clip over the picture, at a stated place and time.", model_backed=False),
    # NOT model_backed, for the same reason as `find` below: a NAMED transition
    # resolves with no provider at all, and that is the common case. But a
    # DESCRIPTIVE `--transition` routes through `resolve_transition`, which asks
    # a model to pick or write the expression -- so the escalation is named here
    # rather than left for a caller to discover after trusting the manifest.
    Capability(
        "stitch",
        "Join clips, with or without a transition. A named transition needs no provider; "
        "describing one in words escalates to a model to choose or write it.",
        model_backed=False,
    ),
    # NOT model_backed: shot detection is ffmpeg and speech is faster-whisper,
    # BOTH LOCAL -- no provider, no network, no credentials. `--vision` is the
    # one path that escalates, reaching `index.describe` -> `vision.describe_shots`,
    # and it is opt-in and already gated behind `--yes` for spend. Named, because
    # a caller reading this to decide "can I index without credentials" must get
    # the true answer for the default invocation AND know what turns it on.
    Capability(
        "index",
        "Build a time-coded account of a video: shots, and speech as timed passages. "
        "Shots and speech are local and need no provider; --vision describes frames with a model.",
        model_backed=False,
    ),
    # NOT model_backed: the literal tier answers any query using words the
    # speaker actually said, with no provider at all, and a DTU run showed that
    # is the common case -- it served a real query on a credential-free box.
    # Marking it model-backed under-sold what works uncredentialed, which is
    # the one thing the spec most wants a consumer to be able to trust.
    Capability(
        "find",
        "Locate a moment by what was said. Literal search needs no provider; describing it by meaning escalates to one.",
        model_backed=False,
    ),
    Capability("audio", "Remove, replace, mix or extract the audio track. Never needs a provider.", model_backed=False),
    Capability(
        "narrate",
        "Write a narration from a prompt, fit it to the timing of the video, and lay it on.",
        model_backed=True,
    ),
    Capability(
        "recolor",
        "Map the palette of a reference image onto the video. Pure arithmetic, no provider.",
        model_backed=False,
    ),
    Capability("vignette", "Darken the edges to pull an eye to the middle. No provider.", model_backed=False),
    Capability(
        "grade", "Apply a named look: warm, cool, punchy, flat, noir, soft, bright. No provider.", model_backed=False
    ),
    Capability("lut", "Apply a .cube lookup table you already have. No provider.", model_backed=False),
    Capability("caption", "Burn subtitles into the picture.", model_backed=False),
    Capability("plan", "Show the edit as JSON, without performing it.", model_backed=False),
    Capability("render", "Compile the plan and encode, once. The ONLY verb that touches pixels.", model_backed=False),
    Capability("verify", "Check a rendered video against named properties. No model involved.", model_backed=False),
    Capability("transitions", "List the 58 transition presets, with what each looks like.", model_backed=False),
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
