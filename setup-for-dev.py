"""
Cross-platform script to set up a development environment for the project.
It assumes that you have installed all the prerequisites listed in CONTRIBUTING.md.
"""

from pathlib import Path
import shlex
import subprocess

REFERENCE_ROOT = Path(__file__).parent / "reference"
REFERENCES = [
    "https://github.com/microsoft/amplifier-smart-tools",
    "https://github.com/github/copilot-sdk",
    "https://github.com/agentskills/agentskills",
]


def run(command: str) -> None:
    """Run a command, forwarding its output to this process's stdout and stderr."""
    subprocess.run(shlex.split(command), check=True)


def clone_missing_references() -> None:
    """Clone every reference repository that is absent; they are gitignored, so a fresh clone has none."""
    for repository in REFERENCES:
        destination = REFERENCE_ROOT / repository.rsplit("/", 1)[-1]
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "--single-branch", repository, str(destination)], check=True)


def main() -> None:
    run("uv --version")
    run("prek --version")
    run("uv sync --frozen --all-extras --all-groups")
    run("prek install")
    clone_missing_references()


if __name__ == "__main__":
    main()
