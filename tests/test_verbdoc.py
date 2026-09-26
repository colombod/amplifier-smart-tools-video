"""Contract tests for verbdoc.py: what `vid <verb> --help` prints.

Guards against four deviations found by the spec's `check-spec-adherence`
review: a documented flag that does not exist (`index --model`), one document
shared by several distinct commands (the `audio` subcommands), a manifest
field described that the schema does not have (`capabilities`), and the
plan-pipe rule appended to commands that do not actually read and write a
plan.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vid.schemas import Manifest
from vid.verbdoc import _LIBRARY_NAMES as LIBRARY_NAMES
from vid.verbdoc import _NO_LIBRARY_SURFACE as NO_LIBRARY_SURFACE
from vid.verbdoc import NOT_PIPE_PARTICIPANTS, PIPE_RULE, verb_doc, verbs

DISTRIBUTION_ROOT = Path(__file__).resolve().parents[1]

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


# ---------------------------------------------------------------------------
# Deviation "capability-skills-complete": every capability skill documented its
# CLI arguments and NONE documented the library's. An agent reading
# `vid trim --help` learned about `source`, `--from` and `--to`, and had no way
# to know `lib.trim` takes a required `plan: Plan` first. Both surfaces ship;
# only one was described.
#
# The section is GENERATED from `inspect.signature`, so these tests pin the tie
# between the prose and the function rather than the wording of 24 blocks.
# ---------------------------------------------------------------------------

LIBRARY_MARKER = "## The same capability from Python"


def test_every_command_documents_its_library_surface() -> None:
    """No capability may describe only its CLI."""
    missing = [name for name in verbs() if name not in NO_LIBRARY_SURFACE and LIBRARY_MARKER not in verb_doc(name)]

    assert not missing, f"these capabilities document no library surface: {', '.join(missing)}"


def test_the_only_exempt_command_is_the_audio_group() -> None:
    """A verb must not join the exempt set by having its function renamed.

    The exemption is an explicit set, not a failed lookup, so this asserts the
    set stays what it claims to be: `audio` is a command GROUP whose own
    document says "None of its own" and points at four subcommands -- each of
    which does carry a real signature.
    """
    assert frozenset({"audio"}) == NO_LIBRARY_SURFACE
    for subcommand in AUDIO_SUBCOMMANDS:
        assert LIBRARY_MARKER in verb_doc(subcommand), f"{subcommand} lost its library surface"


def test_the_documented_signature_is_the_real_one() -> None:
    """Read from the function, so a changed parameter changes the document.

    THIS IS THE POINT OF GENERATING IT. Hand-written signature blocks would
    have satisfied the deviation once and then rotted silently, which is the
    same failure one level up.
    """
    import inspect

    from vid import lib

    for name in verbs():
        if name in NO_LIBRARY_SURFACE:
            continue
        function = getattr(lib, LIBRARY_NAMES.get(name, name))
        rendered = f"lib.{function.__name__}{inspect.signature(function)}"
        assert rendered in verb_doc(name), f"{name}'s documented signature is not {function.__name__}'s actual one"


def test_a_required_plan_parameter_is_called_out_not_just_printed() -> None:
    """`plan` is the one parameter the CLI hides, so it gets a sentence.

    A signature alone would leave a reader to notice that the CLI's OPTIONAL
    `source` and the library's REQUIRED `plan` are different things.
    """
    import inspect

    from vid import lib

    for name in verbs():
        if name in NO_LIBRARY_SURFACE:
            continue
        function = getattr(lib, LIBRARY_NAMES.get(name, name))
        if "plan" not in inspect.signature(function).parameters:
            continue
        assert "`plan` is the edit the operation is appended to" in verb_doc(name), (
            f"{name} takes a plan and does not explain it"
        )


# ---------------------------------------------------------------------------
# Deviations `tool-skill-content` and `capability-skills-complete`, run 8: the
# pipe rule asserted "Every verb except `render` reads a plan on stdin" while
# ELEVEN verbs are registered as non-participants, and it told `recolor` --
# which samples real frames while the plan is built -- that it "needs neither
# ffmpeg nor any AI provider".
#
# Both were false sentences in shipped documentation, which is the same defect
# class as the ONE_SIDED table: a claim nothing re-checks. These tie the prose
# to the registries so neither can drift back.
# ---------------------------------------------------------------------------


def test_the_pipe_rule_does_not_claim_every_verb_participates() -> None:
    """The absolute claim is what made it false; eleven verbs are exempt."""
    assert "Every verb except `render` reads" not in PIPE_RULE, (
        "the pipe rule is asserting again that every verb but `render` reads a plan, "
        f"while these do not: {sorted(NOT_PIPE_PARTICIPANTS)}"
    )


def test_a_verb_that_samples_pixels_while_building_says_so() -> None:
    """`recolor` appends an operation AND needs ffmpeg immediately."""
    from vid.verbdoc import SAMPLES_PIXELS_WHILE_BUILDING

    for name in SAMPLES_PIXELS_WHILE_BUILDING:
        document = verb_doc(name)
        assert "needs neither ffmpeg" not in document, f"{name} samples frames but claims it needs no ffmpeg"
        assert "DOES need ffmpeg right now" in document, f"{name} does not warn that it decodes while building"


def test_the_ordinary_plan_verbs_keep_the_free_claim() -> None:
    """The correction must not have taken the true claim with it."""
    from vid.verbdoc import SAMPLES_PIXELS_WHILE_BUILDING

    for name in verbs():
        if name in NOT_PIPE_PARTICIPANTS or name in SAMPLES_PIXELS_WHILE_BUILDING:
            continue
        assert "needs neither ffmpeg" in verb_doc(name), f"{name} lost the (true) no-prerequisites claim"


def test_a_verb_that_samples_pixels_really_does_preflight_ffmpeg(monkeypatch) -> None:
    """The set is a claim about the CODE, so check the code.

    A hand-maintained set could name the wrong verb and every test above would
    still pass. This asserts the named verb genuinely refuses without ffmpeg.
    """
    from vid import color, probe
    from vid.schemas import VidError
    from vid.verbdoc import SAMPLES_PIXELS_WHILE_BUILDING

    assert frozenset({"recolor"}) == SAMPLES_PIXELS_WHILE_BUILDING

    monkeypatch.setattr(probe, "have_ffmpeg", lambda: False)
    with pytest.raises(VidError):
        color._require_ffmpeg_tools()


def test_the_manifest_does_not_repeat_the_false_pipe_claim() -> None:
    """The same sentence shipped twice; correcting one copy is not correcting it."""
    manifest_body = (DISTRIBUTION_ROOT / "src/vid/SMART_TOOL.md").read_text(encoding="utf-8")

    assert "Every verb except `render` reads an edit plan" not in manifest_body, (
        "SMART_TOOL.md is asserting again that every verb but `render` reads a plan"
    )
    assert "recolor" in manifest_body, "the manifest does not mention recolor's build-time ffmpeg need"
