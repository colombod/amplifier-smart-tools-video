"""Contract test for skills/vid/SKILL.md: the Agent Skill is thin.

Guards against a deviation found by the spec's `check-spec-adherence` review:
the skill duplicated a capability-to-prerequisite matrix and the runtime
chaining contract, both of which can drift from what `vid --help` and
`vid check` actually say. Per spec: "The skill carries the manifest's name and
description, the install commands, and the instruction to run `--help` and
follow it. Nothing more."
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.fixtures import have_ffmpeg

SKILL_PATH = Path(__file__).parents[1] / "skills" / "vid" / "SKILL.md"

# Content that used to live in the skill and must now only be reachable from
# runtime help (`vid check`, `vid --help`, `vid <verb> --help`) instead.
REMOVED_MARKERS = (
    "capability | needs | install",  # the matrix table header
    "brew postinstall ca-certificates",  # the caption/libass troubleshooting steps
    "Chain the verbs. Do not call them one at a time.",  # the chaining contract restatement
    # Trimmed after `check-spec-adherence` run 3 flagged `agent-skill-is-thin`
    # again. The INSTALL COMMANDS all stayed -- the spec names them explicitly --
    # but the prose DESCRIBING what each extra enables went, because that is the
    # content that drifts: `vid check` reports what is actually present and
    # configured on the box, and this file cannot.
    "local transcription, so moments can be",  # per-extra capability prose
    "speaks narration through OpenAI's API",  # per-extra capability prose
    "the CLI is a thin wrapper",  # library-surface description
    "against the repository's",  # version-comparison mechanics
)


def _frontmatter_and_body() -> tuple[dict, str]:
    text = SKILL_PATH.read_text(encoding="utf-8")
    _, _, rest = text.partition("---\n")
    frontmatter_text, _, body = rest.partition("\n---\n")
    return yaml.safe_load(frontmatter_text), body


def test_frontmatter_carries_name_and_description() -> None:
    frontmatter, _ = _frontmatter_and_body()

    assert frontmatter["name"] == "vid"
    assert frontmatter["description"]


def test_body_carries_install_commands() -> None:
    _, body = _frontmatter_and_body()

    assert "uv tool install" in body
    assert "uv add" in body


def test_body_instructs_running_help_and_following_it() -> None:
    _, body = _frontmatter_and_body()

    assert "vid --help" in body
    assert "vid <verb> --help" in body
    assert "vid check" in body


def test_body_does_not_duplicate_runtime_help_content() -> None:
    _, body = _frontmatter_and_body()

    for marker in REMOVED_MARKERS:
        assert marker not in body, f"skill duplicates runtime-help content: {marker!r}"


@pytest.mark.skipif(not have_ffmpeg(), reason="exercises vid check's ffmpeg-dependent branch")
def test_removed_libass_troubleshooting_is_reachable_via_vid_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The macOS libass note that used to be duplicated in the skill is not
    lost -- forcing the missing-subtitles-filter branch of `vid check` proves
    the same remedy is still reachable from runtime help.
    """
    from vid import lib, probe

    monkeypatch.setattr(probe, "has_subtitles_filter", lambda: False)

    assert "brew postinstall ca-certificates fontconfig gnutls glib openssl@3" in lib.check()


def test_removed_chaining_contract_is_reachable_via_the_manifest_body() -> None:
    """The chaining contract that used to be restated in the skill is the same
    text `vid --help` renders from the manifest body.
    """
    from vid import lib

    manifest_body = lib.load_manifest().body

    assert "chain the verbs" in manifest_body.lower()
