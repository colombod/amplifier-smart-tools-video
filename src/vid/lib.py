"""Top level entry point for the Vid library."""

from pathlib import Path

from vid.core import manifest
from vid.core import skill as skill_module
from vid.schemas import Manifest


def load_manifest() -> Manifest:
    """The tool's manifest as structured data, read from the SMART_TOOL.md shipped inside the package."""
    return manifest.load_manifest()


def skill() -> str:
    """The tool's skill: the manifest body and the capability list, wrapped so a reader knows where its files are."""
    return skill_module.skill()


def skill_directory() -> Path:
    """The installed package root, where the files the skill names can be read."""
    return skill_module.skill_directory()


def skill_resources() -> list[str]:
    """The files the skill lists, as paths relative to the skill directory. Every one ships inside the package."""
    return skill_module.skill_resources()


def repository_url() -> str | None:
    """The tool's canonical source, from the package metadata, or None when the package declares none."""
    return skill_module.repository_url()


def render(plan, output: str, *, print_command: bool = False) -> str:
    """Compile a plan and run it, or show what would run.

    Probing happens here rather than in the compiler: durations are a property of
    the files, not of the plan, and keeping the compiler pure is what lets every
    other verb work with no ffmpeg installed.
    """
    import subprocess

    from vid.compile import compile_plan
    from vid.plan import Stitch
    from vid.probe import duration, have_ffmpeg
    from vid.schemas import VidError

    needs_durations = any(isinstance(op, Stitch) and op.transition for op in plan.operations)
    durations: dict[str, float] = {}
    if needs_durations:
        paths = [plan.source] + [s for op in plan.operations if isinstance(op, Stitch) for s in op.sources]
        durations = {path: duration(path) for path in dict.fromkeys(p for p in paths if p and p != "-")}

    command = compile_plan(plan, output, durations=durations)

    if print_command:
        import shlex

        return " ".join(shlex.quote(part) for part in command)

    if not have_ffmpeg():
        raise VidError(
            "ffmpeg is not on PATH, and rendering is the one thing that needs it. "
            "Run `vid check` for the install command for your system, or re-run with "
            "--print-command to see the invocation without running it."
        )
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-12:])
        raise VidError(f"ffmpeg failed:\n{tail}")
    return output


def check() -> str:
    """A report on this machine: which tier is reachable, and what would unlock the next."""
    import importlib.util
    import shutil

    lines = ["vid -- what this installation can do", ""]
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")

    if ffmpeg:
        lines += [f"  [ok]      ffmpeg          {ffmpeg}", "            every mechanical verb works, and render can run."]
    else:
        lines += [
            "  [missing] ffmpeg",
            "            Plans still build without it -- only `render` needs it.",
            "            macOS:   brew install ffmpeg",
            "            Linux:   apt install ffmpeg   (or your distribution's package)",
            "            Windows: winget install Gyan.FFmpeg",
        ]
    if ffmpeg and not ffprobe:
        lines.append("  [warn]    ffprobe missing -- transitions cannot be placed without it.")

    speech = importlib.util.find_spec("faster_whisper") is not None
    lines.append("")
    if speech:
        lines.append("  [ok]      speech backend  faster-whisper")
    else:
        lines += [
            "  [missing] speech backend",
            "            Unlocks: index speech, and find-by-what-was-said.",
            "            uv tool install --force 'vid[speech] @ git+https://github.com/colombod/amplifier-smart-tools-video'",
        ]

    lines += ["", "  Nothing here is sent anywhere. This is a report on your machine."]
    return "\n".join(lines)
