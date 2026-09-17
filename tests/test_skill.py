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
