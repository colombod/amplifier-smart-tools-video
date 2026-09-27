"""No shipped surface may claim that EVERY verb passes a plan. Eleven do not.

THE SENTENCE THAT WOULD NOT DIE. `vid` has 25 capabilities. Eight of them --
`index`, `find`, `narrate`, `verify`, `check`, `manifest`, `transitions` and
`audio extract` -- report or read rather than appending to a plan, and are
registered as such in `verbdoc.NOT_PIPE_PARTICIPANTS`. A ninth, `recolor`,
appends an operation but samples real frames WHILE BUILDING, so it needs ffmpeg
before `render`.

Some variation of "every verb except `render`..." was nevertheless shipped in
NINE places, found only by grepping for it after fixing the first two:

    src/vid/verbdoc.py          the pipe rule appended to 14 capabilities
    src/vid/SMART_TOOL.md       the manifest body, twice more in description/use_cases
    docs/ARCHITECTURE.md        twice
    src/vid/plan.py             the module docstring AND a user-facing error
    src/vid/probe.py            the module docstring
    README.md                   the opening description
    pyproject.toml              the PACKAGE description -- it reaches PyPI metadata
    skills/vid/SKILL.md         the shipped Agent Skill
    tests/                      a test docstring and an overclaiming test NAME

Nine copies of one sentence, and correcting any one of them left the other
eight asserting the opposite. That is the defect this whole branch is about, in
prose instead of code: a claim with no single owner is a claim nobody can keep
true.

So it is enumerated here rather than remembered. Adding a tenth copy fails.
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

#: Where a reader or an agent could actually encounter a claim.
SHIPPED_SURFACES = ("*.md", "*.py", "*.toml", "*.json")

#: Directories that are build output, dependencies, or history -- not surfaces
#: this repository ships or controls.
SKIP_PARTS = frozenset({".git", ".venv", "_site", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache"})

#: The overclaims. Each says, in some wording, that the pipe is universal.
OVERCLAIMS = (
    re.compile(r"every verb passes an edit plan", re.IGNORECASE),
    re.compile(r"every verb except `?render`? (reads|writes|is|works)", re.IGNORECASE),
    re.compile(r"\bevery verb reads a plan\b", re.IGNORECASE),
    re.compile(r"nothing decodes a frame until `?render`?\. that is the whole", re.IGNORECASE),
)

#: This file necessarily quotes the sentences it bans, as does the test that
#: guards the pipe rule itself.
#:
#: CHANGELOG.md is exempt for a different and more interesting reason: its
#: `0.1.0` entry says "every verb except `render` reads a plan on stdin", and at
#: 0.1.0 -- "nine verbs, a plan that travels between them" -- THAT WAS TRUE. The
#: claim only became false later, as `index`, `find`, `narrate`, `verify` and the
#: other reporting verbs were added and nobody revisited the sentence. A changelog
#: records what a release was; editing it to match what the tool became would be
#: falsifying history to satisfy a linter. The entry stays.
EXEMPT_FILES = frozenset({"test_docs_do_not_overclaim_the_pipe.py", "test_verbdoc.py", "CHANGELOG.md"})


def _shipped_files() -> list[Path]:
    found: list[Path] = []
    for pattern in SHIPPED_SURFACES:
        for path in ROOT.rglob(pattern):
            if SKIP_PARTS & set(path.parts):
                continue
            if path.name in EXEMPT_FILES:
                continue
            found.append(path)
    return found


def test_no_shipped_surface_claims_the_pipe_is_universal() -> None:
    offenders: list[str] = []
    for path in _shipped_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.split("\n"), 1):
            for pattern in OVERCLAIMS:
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:90]}")

    assert not offenders, (
        "these shipped surfaces claim every verb passes a plan, which is false for the "
        "eight registered non-participants and for `recolor`:\n  " + "\n  ".join(offenders)
    )


def test_the_detector_would_catch_a_tenth_copy(tmp_path) -> None:
    """A guard that cannot fire guards nothing."""
    bad = tmp_path / "README.md"
    bad.write_text("Chainable - every verb passes an edit plan, and one render compiles it.\n")

    hits = [p for p in OVERCLAIMS if p.search(bad.read_text())]
    assert hits, "the detector does not recognise the exact sentence that shipped nine times"


def test_the_surfaces_being_scanned_actually_exist() -> None:
    """Guards against the scan silently covering nothing."""
    files = _shipped_files()
    names = {p.name for p in files}

    assert len(files) > 30, f"only {len(files)} shipped files found; the scan is looking in the wrong place"
    for required in ("README.md", "pyproject.toml", "SMART_TOOL.md", "ARCHITECTURE.md"):
        assert required in names, f"{required} is not being scanned"
