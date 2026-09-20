"""CopilotIntelligence's preflight failures must never disagree with the
manifest. `SMART_TOOL.md` declares an install reference for `gh` and for the
GitHub Copilot subscription; a preflight failure that names something else
(or nothing) is a defect the moment the two drift apart, so the failure
messages read the manifest's own reference rather than a second, hand-copied
copy of the same URL.
"""

from __future__ import annotations

import pytest

from vid.core.manifest import load_manifest
from vid.intelligence.copilot import CopilotIntelligence, _manifest_install
from vid.schemas import VidError


def test_manifest_install_reads_the_reference_the_manifest_actually_declares():
    manifest = load_manifest()
    gh_requirement = next(requirement for requirement in manifest.requires if requirement.name == "gh")

    assert _manifest_install("gh") == gh_requirement.install
    assert _manifest_install("gh") == "https://cli.github.com/"


def test_manifest_install_returns_empty_for_a_requirement_the_manifest_does_not_declare():
    assert _manifest_install("not-a-real-requirement") == ""


def test_missing_gh_names_the_manifests_own_install_reference(monkeypatch, tmp_path):
    """A real, empty PATH -- not a stubbed `shutil.which` -- so this proves the
    actual preflight code path, not an assumption about how it calls `which`."""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    with pytest.raises(VidError) as failure:
        CopilotIntelligence()._github_token()

    message = str(failure.value)
    assert "https://cli.github.com/" in message, "the failure must cite the manifest's own gh install reference"
    assert "gh auth login" in message


def test_not_signed_in_names_the_copilot_subscription_reference(monkeypatch, tmp_path):
    """A real, minimal `gh` stand-in on PATH that behaves like a signed-out
    CLI -- `gh auth token` exits non-zero -- so `_github_token` genuinely
    shells out and genuinely fails, rather than a mocked subprocess result."""
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    gh_script = fake_bin / "gh"
    gh_script.write_text("#!/bin/sh\necho 'not logged in' 1>&2\nexit 1\n", encoding="utf-8")
    gh_script.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    with pytest.raises(VidError) as failure:
        CopilotIntelligence()._github_token()

    message = str(failure.value)
    assert "https://github.com/github/copilot-cli#prerequisites" in message, (
        "the failure must cite the manifest's own Copilot subscription reference"
    )
    assert "gh auth login" in message
