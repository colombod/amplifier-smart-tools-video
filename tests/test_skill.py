import pytest
from typer.testing import CliRunner

from vid.cli import app
from vid.core import skill as skill_module
from vid.core.skill import CAPABILITIES
from vid.lib import load_manifest, skill, skill_directory, skill_resources

RELATIVE_PATHS_LINE = "Relative paths in this skill are relative to the skill directory."

runner = CliRunner()


def test_manifest_body_is_the_markdown_below_the_frontmatter() -> None:
    body = load_manifest().body

    assert body
    assert not body.startswith("#")


def test_skill_is_a_wrapped_document_naming_every_capability() -> None:
    document = skill()

    assert document.startswith('<skill_content name="vid">')
    assert document.endswith("</skill_content>")
    assert "# vid" in document
    assert load_manifest().body in document
    for capability in CAPABILITIES:
        assert f"`{capability.name}` [" in document
        assert f"`vid {capability.name} --help`" in document


def test_repository_line_is_omitted_when_the_package_declares_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(skill_module, "repository_url", lambda: None)
    header = skill_module.skill().splitlines()

    assert "Repository:" not in skill_module.skill()
    assert header[2] == RELATIVE_PATHS_LINE


def test_repository_line_sits_between_the_header_lines_when_declared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(skill_module, "repository_url", lambda: "https://example.invalid/vid")
    header = skill_module.skill().splitlines()

    assert header[1].startswith("Skill directory: ")
    assert header[2] == "Repository: https://example.invalid/vid"
    assert header[3] == RELATIVE_PATHS_LINE


def test_skill_resources_resolve_under_the_skill_directory() -> None:
    root = skill_directory()
    resources = [line.removeprefix("<file>").removesuffix("</file>") for line in _resource_lines(skill())]

    assert resources
    assert resources == skill_resources()
    assert (root / "SMART_TOOL.md").is_file()
    for resource in resources:
        assert (root / resource).is_file()


def _resource_lines(document: str) -> list[str]:
    lines = [line.strip() for line in document.splitlines()]
    start = lines.index("<skill_resources>")
    end = lines.index("</skill_resources>")
    return lines[start + 1 : end]


def test_help_prints_the_skill() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert result.stdout.strip() == skill()


def test_short_help_and_no_arguments_print_the_terse_summary() -> None:
    short = runner.invoke(app, ["-h"])
    bare = runner.invoke(app, [])

    assert short.exit_code == 0
    assert "<skill_content" not in short.stdout
    assert "manifest" in short.stdout
    assert "<skill_content" not in bare.stdout


def test_every_command_answers_its_own_help() -> None:
    for capability in CAPABILITIES:
        result = runner.invoke(app, [capability.name, "--help"])

        assert result.exit_code == 0
        assert result.stdout.strip()


# ---------------------------------------------------------------------------
# A capability that says `model_backed=False` is read by a caller deciding
# "can I run this with no credentials at all". The spec's core promise is that
# deterministic capabilities do exactly that, so a capability with a provider
# path hiding behind a flag must NAME it -- the classification is per-capability
# while the behaviour is per-path.
#
# `find` established the pattern in this same table: stay model_backed=False
# where a genuinely useful credential-free path exists, and say in the summary
# what turns a provider on. Marking the whole capability model-backed would
# under-sell what works uncredentialed, which is the thing the spec most wants
# a consumer to be able to trust.
# ---------------------------------------------------------------------------

#: (capability, the flag or input that escalates to a provider)
ESCALATING = [
    ("stitch", "describing a transition in words rather than naming one"),
    ("index", "--vision, which describes frames with a model"),
    ("find", "describing a moment by meaning rather than literal words"),
]


@pytest.mark.parametrize(("name", "what_escalates"), ESCALATING, ids=[c[0] for c in ESCALATING])
def test_a_capability_with_a_provider_path_names_it(name, what_escalates):
    """Verified against the source, not taken on the summary's word:

    - stitch  -> lib.resolve_transition takes `model` and calls it for a
                 DESCRIPTIVE transition; a NAME resolves with no provider.
    - index   -> the CLI's --vision reaches index.describe -> vision.describe_shots,
                 and is already gated behind --yes for spend. Shots are ffmpeg
                 and speech is local faster-whisper: neither needs credentials.
    """
    from vid.core.skill import CAPABILITIES

    capability = next(c for c in CAPABILITIES if c.name == name)

    assert not capability.model_backed, (
        f"{name} is now model_backed=True; if its deterministic path was removed this test is stale, "
        "and if it was not, the manifest now under-sells what runs with no credentials"
    )
    assert "provider" in capability.summary or "model" in capability.summary, (
        f"{name} has a provider path ({what_escalates}) that its summary does not mention. "
        "A caller reading the manifest to decide whether credentials are needed gets the wrong answer."
    )


def test_a_capability_with_no_provider_path_does_not_claim_one():
    """The control. Without it, 'mentions a provider' could be satisfied by
    saying so on every capability, which would be just as useless."""
    from vid.core.skill import CAPABILITIES

    deterministic = {"trim", "cut", "retime", "zoom", "vignette", "lut", "caption"}
    for capability in CAPABILITIES:
        if capability.name in deterministic:
            assert not capability.model_backed, f"{capability.name} became model-backed"
            assert "escalat" not in capability.summary, (
                f"{capability.name} has no provider path but its summary implies an escalation"
            )
