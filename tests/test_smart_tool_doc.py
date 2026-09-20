"""Contract tests for SMART_TOOL.md: the manifest frontmatter and body.

Guards against two deviations found by the spec's `check-spec-adherence`
review: the manifest body never told an agent what `vid` is NOT for, and the
manifest never declared Piper -- a prerequisite `narrate` actually checks for
at runtime -- as a requirement.
"""

from __future__ import annotations

from vid.lib import load_manifest, skill

# Agent Skills convention: a rendered skill should stay well under this.
SKILL_LINE_CEILING = 500


def test_manifest_body_names_what_vid_is_not_for() -> None:
    body = load_manifest().body

    assert "when not to use" in body.lower()
    for non_goal in ("image", "audio-only", "download"):
        assert non_goal in body.lower()


def test_rendered_skill_stays_under_the_line_ceiling() -> None:
    rendered = skill()

    assert len(rendered.splitlines()) < SKILL_LINE_CEILING


def test_manifest_declares_piper_as_an_optional_requirement() -> None:
    manifest = load_manifest()
    by_name = {requirement.name: requirement for requirement in manifest.requires}

    assert "piper-tts" in by_name
    piper = by_name["piper-tts"]
    assert piper.optional is True
    assert piper.purpose
    assert piper.install


def test_every_requirement_has_the_same_shape() -> None:
    """A conformance rule checks this shape; guard it locally too."""
    manifest = load_manifest()

    for requirement in manifest.requires:
        assert requirement.name
        assert requirement.purpose
        assert requirement.install
        assert isinstance(requirement.optional, bool)


def test_manifest_version_matches_pyproject() -> None:
    from importlib.metadata import version

    assert load_manifest().version == version("vid")
