"""Contract tests for verbdoc.py: what `vid <verb> --help` prints.

Guards against four deviations found by the spec's `check-spec-adherence`
review: a documented flag that does not exist (`index --model`), one document
shared by several distinct commands (the `audio` subcommands), a manifest
field described that the schema does not have (`capabilities`), and the
plan-pipe rule appended to commands that do not actually read and write a
plan.
"""

from __future__ import annotations

from vid.schemas import Manifest
from vid.verbdoc import NOT_PIPE_PARTICIPANTS, PIPE_RULE, verb_doc, verbs

PIPE_MARKER = "The pipe carries a plan, not pixels"

AUDIO_SUBCOMMANDS = ("audio_remove", "audio_replace", "audio_mix", "audio_extract")


def test_every_registered_command_has_its_own_document() -> None:
    """Every doc key is distinct text -- no two commands share a body verbatim."""
    bodies = [verb_doc(name) for name in verbs()]

    assert len(bodies) == len(set(bodies)), "two or more commands render the identical document"


def test_index_documents_the_model_flag_and_its_default() -> None:
    doc = verb_doc("index")

    assert "--model" in doc
    assert "base" in doc
    for size in ("tiny", "small", "medium"):
        assert size in doc


def test_audio_subcommands_are_documented_separately() -> None:
    """cli.py wires each audio subcommand's --help to one of these four keys.

    (Today it still points all four at "audio" -- see the report filed
    alongside this change. This test locks in the target shape so that wiring
    fix is verifiable once applied.)
    """
    docs = {name: verb_doc(name) for name in AUDIO_SUBCOMMANDS}

    assert len(set(docs.values())) == len(docs), "the four audio subcommands must not share a document"

    assert "audio remove" in docs["audio_remove"]
    assert "audio replace" in docs["audio_replace"]
    assert "--with" in docs["audio_replace"]
    assert "audio mix" in docs["audio_mix"]
    assert "--with" in docs["audio_mix"]
    assert "--level" in docs["audio_mix"]
    assert "-18.0" in docs["audio_mix"] or "-18" in docs["audio_mix"]
    assert "audio extract" in docs["audio_extract"]
    # extract takes two required positional arguments and never continues a
    # piped plan -- the opposite of remove/replace/mix.
    assert "output" in docs["audio_extract"]
    assert PIPE_MARKER not in docs["audio_extract"]


def test_audio_group_overview_points_at_each_subcommand_help() -> None:
    doc = verb_doc("audio")

    for sub in ("remove", "replace", "mix", "extract"):
        assert f"vid audio {sub} --help" in doc


def test_manifest_doc_matches_the_manifest_schema() -> None:
    doc = verb_doc("manifest")
    schema_fields = set(Manifest.model_fields)

    # The old doc claimed a `capabilities` field. The schema has no such field --
    # capability-level detail (model-backed or not) lives in the skill, not the manifest.
    assert "capabilities" not in schema_fields
    assert "**`capabilities`**" not in doc

    # Every field the doc calls out by name must actually exist on the schema.
    for field in ("use_cases", "platforms", "requires", "body"):
        assert field in schema_fields
        assert f"`{field}`" in doc


def test_plan_pipe_rule_only_applies_to_commands_that_consume_and_emit_a_plan() -> None:
    for name in verbs():
        doc = verb_doc(name)
        if name in NOT_PIPE_PARTICIPANTS:
            assert PIPE_MARKER not in doc, f"{name!r} does not consume+emit a plan but claims to"
        else:
            assert PIPE_MARKER in doc, f"{name!r} consumes+emits a plan but the doc omits the pipe rule"


def test_not_pipe_participants_covers_the_verbs_that_never_touch_the_pipe() -> None:
    # index, narrate, find and verify each always take a video/file argument
    # rather than reading a plan from stdin -- named explicitly since these were
    # the ones found rendering the pipe rule incorrectly.
    for name in ("index", "narrate", "find", "verify", "manifest", "transitions", "audio_extract"):
        assert name in NOT_PIPE_PARTICIPANTS
        assert PIPE_RULE.strip() not in verb_doc(name)


def test_find_doc_does_not_claim_to_read_stdin() -> None:
    doc = verb_doc("find")

    assert "never reads a plan from stdin" in doc


def test_narrate_doc_does_not_claim_to_touch_the_plan_pipe() -> None:
    doc = verb_doc("narrate")

    assert "does not touch the edit-plan pipe" in doc
