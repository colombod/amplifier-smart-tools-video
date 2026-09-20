"""Drift guard: `vid <verb> --help` cannot silently fall out of sync with the CLI.

Guards against the fifth deviation found by the spec's `check-spec-adherence`
review (`capability-skills-complete`): a capability document must carry a
complete argument/default inventory, and that inventory must be generated or
validated from the real command signature so it cannot drift -- a hand-written
document with no guard is what produced the finding in the first place.

This introspects the actual Click command tree Typer builds from `vid.cli.app`
(via `typer.main.get_command`) rather than trusting anything written by hand,
and checks two directions for every registered command, including the four
nested `audio` subcommands:

1. every parameter the parser accepts appears somewhere in that command's
   document (so a new flag added to `cli.py` and never documented fails here);
2. no document's **Arguments.** section names a flag that command does not
   accept (so a renamed or removed flag left behind in prose fails here too).

The second check is confined to the **Arguments.** ... **Result.** section of
each document, not the whole prose body. Several documents deliberately
mention flag-shaped text that belongs to a DIFFERENT command or tool for
illustration -- `vid grade --help` explains why `--warmth`/`--contrast` are
NOT offered, and `vid index --help` shows a `uv tool install --force` line.
Scanning the whole document for `--xxx` tokens would flag both as invented
parameters; scanning only the structured Arguments section does not, because
that section is never used for anything but this command's own parameters.

NOT `isinstance(..., click.Argument)` / `isinstance(..., click.Group)`. Typer
0.27 vendors its own fork of Click (`typer._click`), so the objects
`get_command` returns are `typer.core.Typer*` subclasses of *that* fork, not
of the `click` package this file could otherwise import -- an isinstance
check against `click.Argument`/`click.Group` is always False against them and
fails silent (an empty loop, every assertion vacuously true). Duck-typing on
`param.param_type_name` (Click's own public discriminator: `"argument"` vs
`"option"`) and `hasattr(command, "commands")` works against either fork, and
`test_the_command_tree_actually_has_commands_in_it` exists specifically so a
future refactor that reintroduces that mistake fails loudly instead of quietly
passing everything.
"""

from __future__ import annotations

import re

from typer.main import get_command

from vid.cli import app
from vid.verbdoc import verb_doc, verbs

# Universal to every command via the two-help-systems convention (see the
# module docstring in verbdoc.py): `-h`/`--help` exists on every command and is
# never itself a domain argument, so it is never expected in a document's
# Arguments section. The root callback also carries `--version`,
# `--install-completion` and `--show-completion`, none of which are capabilities.
_UNIVERSAL = {
    "help",
    "-h",
    "--help",
    "version",
    "--version",
    "install_completion",
    "--install-completion",
    "show_completion",
    "--show-completion",
}

# Command path, as Typer/Click nests it (empty tuple is the root `vid` callback
# itself, which is the skill rather than a capability document) -> the
# verbdoc.py key that documents it.
_DOC_KEY: dict[tuple[str, ...], str | None] = {
    (): None,
    ("manifest",): "manifest",
    ("trim",): "trim",
    ("cut",): "cut",
    ("retime",): "retime",
    ("zoom",): "zoom",
    ("stitch",): "stitch",
    ("caption",): "caption",
    ("plan",): "plan",
    ("render",): "render",
    ("index",): "index",
    ("find",): "find",
    ("narrate",): "narrate",
    ("recolor",): "recolor",
    ("vignette",): "vignette",
    ("grade",): "grade",
    ("lut",): "lut",
    ("verify",): "verify",
    ("transitions",): "transitions",
    ("check",): "check",
    ("audio",): "audio",
    ("audio", "remove"): "audio_remove",
    ("audio", "replace"): "audio_replace",
    ("audio", "mix"): "audio_mix",
    ("audio", "extract"): "audio_extract",
}

_ARGUMENTS_SECTION = re.compile(r"\*\*Arguments\.\*\*(.*?)\*\*Result\.\*\*", re.DOTALL)
_FLAG_TOKEN = re.compile(r"--[a-zA-Z][a-zA-Z-]*")


def _iter_commands(command, prefix: tuple[str, ...] = ()):
    yield prefix, command
    # Duck-typed rather than `isinstance(command, click.Group)` -- see the
    # module docstring for why an isinstance check against the wrong Click
    # fork silently drops every subcommand instead of failing.
    for name, sub in getattr(command, "commands", {}).items():
        yield from _iter_commands(sub, (*prefix, name))


def _param_tokens(command) -> list[str]:
    """Every token a caller could type for one of this command's own parameters."""
    tokens: list[str] = []
    for param in command.params:
        # `param_type_name` is Click's own public discriminator ("argument" vs
        # "option"), stable across the `click` package and Typer's fork of it.
        if param.param_type_name == "argument":
            tokens.append(param.name or "")
        else:
            tokens.extend(param.opts)
            tokens.extend(getattr(param, "secondary_opts", ()))
    return [t for t in tokens if t]


def _registered_commands() -> list[tuple[tuple[str, ...], object]]:
    root = get_command(app)
    return [(path, command) for path, command in _iter_commands(root) if path != ()]


def test_the_command_tree_actually_has_commands_in_it() -> None:
    """A canary for the isinstance mistake described in the module docstring.

    If command-tree traversal ever silently breaks again (wrong type used for
    the group check), `_registered_commands()` returns `[]` and every test
    below passes vacuously -- on an empty loop, with nothing checked. This
    fixed floor (20 capabilities + 4 audio subcommands, see `core/skill.py`)
    means that failure mode fails loudly here instead.
    """
    assert len(_registered_commands()) >= 24


def test_every_registered_command_is_mapped_to_a_document() -> None:
    """A command with no entry here would silently skip both drift checks below."""
    for path, _command in _registered_commands():
        assert path in _DOC_KEY, (
            f"`vid {' '.join(path)}` is registered but has no entry in _DOC_KEY. "
            "Add one (and give it a document in verbdoc.py) before this test can guard it."
        )


def test_every_documented_key_is_a_registered_command() -> None:
    """The inverse of the mapping above: no orphaned document for a command that no longer exists."""
    mapped = {key for key in _DOC_KEY.values() if key is not None}
    assert mapped == set(verbs())


def test_every_real_parameter_appears_in_its_command_document() -> None:
    for path, command in _registered_commands():
        doc_key = _DOC_KEY[path]
        if doc_key is None:
            continue
        doc = verb_doc(doc_key)
        for token in _param_tokens(command):
            if token in _UNIVERSAL:
                continue
            assert token in doc, (
                f"`vid {' '.join(path)}` accepts {token!r}, but its document "
                f"({doc_key!r}) never mentions it. Document the flag (with its default) "
                "or the two have drifted apart."
            )


def test_no_document_names_a_flag_its_command_does_not_accept() -> None:
    for path, command in _registered_commands():
        doc_key = _DOC_KEY[path]
        if doc_key is None:
            continue
        doc = verb_doc(doc_key)
        section = _ARGUMENTS_SECTION.search(doc)
        assert section is not None, f"{doc_key!r} has no **Arguments.** ... **Result.** section for this test to check."
        accepted = set(_param_tokens(command)) | _UNIVERSAL
        for flag in set(_FLAG_TOKEN.findall(section.group(1))):
            assert flag in accepted, (
                f"{doc_key!r}'s Arguments section names {flag!r}, which "
                f"`vid {' '.join(path)}` does not accept. The document has drifted ahead of the code."
            )


def test_every_document_has_arguments_result_and_failures_sections() -> None:
    for key in verbs():
        doc = verb_doc(key)
        assert "**Arguments.**" in doc, f"{key!r} has no Arguments section"
        assert "**Result.**" in doc, f"{key!r} has no Result section"
        assert "**Failures.**" in doc, f"{key!r} has no Failures section"
