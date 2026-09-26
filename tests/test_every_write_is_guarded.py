"""Every filesystem write in this tool converts its failure into a VidError.

WHY THIS FILE EXISTS, and it is the same story as the model-guard ratchet one
directory over. `cli.main` translates only `VidError` into a named, non-zero
exit, so a filesystem call that can raise `OSError` and is not converted
reaches the user as a bare traceback naming neither what failed nor what to do.

Eleven such calls shipped, and they were found one or two at a time, over four
rounds, each time by someone else:

    index.save          reported by a review, fixed -- names VID_INDEX_DIR
    index.load          found by sweeping its sibling
    index.fingerprint   found by a test written for that sibling
    lib narration       reported by check-spec-adherence run 6
    openai_tts x2       reported by check-spec-adherence run 8
    voice x2            never reported
    vision, compile,    never reported -- found only by enumerating
    color, narrate

Every round fixed the reported instance and left the rest. That is why the
eleventh was still there after four rounds of careful fixing, and it is
exactly the failure the ONE_SIDED table taught: a property checked one cited
example at a time is not checked.

So the property is ENUMERATED here instead. This is a structural test over the
whole source tree, not a list of sites someone remembered to add.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

DISTRIBUTION_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = DISTRIBUTION_ROOT / "src" / "vid"

#: Calls that create or write a file or directory.
WRITE_CALLS = frozenset({"mkdir", "write_text", "write_bytes", "copyfile", "copy", "copy2", "move"})

#: Handlers that genuinely convert a filesystem failure.
CONVERTING = frozenset({"OSError", "IOError", "Exception", "BaseException"})

#: How far above a write the `with writing(...)` may sit. Generous enough for
#: a mkdir/write pair with a comment between, tight enough that an unrelated
#: block further up cannot be mistaken for a guard.
WITHIN_LINES = 12


def _converts_oserror(handler: ast.ExceptHandler) -> bool:
    if isinstance(handler.type, ast.Name):
        return handler.type.id in CONVERTING
    if isinstance(handler.type, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in CONVERTING for e in handler.type.elts)
    return False


def _inside_converting_try(tree: ast.AST, lineno: int) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        covered: list[int] = [
            n.lineno for branch in node.body for n in ast.walk(branch) if isinstance(n, ast.stmt | ast.expr)
        ]
        if covered and min(covered) <= lineno <= max(covered) and any(_converts_oserror(h) for h in node.handlers):
            return True
    return False


def _inside_writing_block(source_lines: list[str], lineno: int) -> bool:
    start = max(0, lineno - WITHIN_LINES)
    return any("with writing(" in line for line in source_lines[start:lineno])


def _write_sites() -> list[tuple[Path, int, str]]:
    found = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        lines = source.split("\n")
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in WRITE_CALLS:
                continue
            # `"a,b".copy()` and friends: a literal receiver is never a path.
            if isinstance(node.func.value, ast.Constant):
                continue
            if _inside_converting_try(tree, node.lineno) or _inside_writing_block(lines, node.lineno):
                continue
            found.append((path.relative_to(DISTRIBUTION_ROOT), node.lineno, node.func.attr))
    return found


def test_no_filesystem_write_escapes_as_a_traceback() -> None:
    """A twelfth unguarded write cannot be added without failing here."""
    unguarded = _write_sites()

    assert not unguarded, (
        "these filesystem writes convert no OSError, so they reach the caller as a bare "
        "traceback instead of a named VidError: "
        + ", ".join(f"{path}:{line} .{call}()" for path, line, call in unguarded)
        + ". Wrap them in `vid.core.writes.writing(destination, what, remedy)`, naming the "
        "environment variable that chose the location where there is one."
    )


def test_the_enumeration_can_actually_see_an_unguarded_write(tmp_path) -> None:
    """The detector must FAIL on a known-bad file, or it proves nothing.

    A structural test that cannot fail is the same defect it exists to catch --
    the ONE_SIDED table passed for months while six of its claims were false.
    This drives the same AST logic over a file written to be wrong.
    """
    bad = tmp_path / "unguarded.py"
    bad.write_text("from pathlib import Path\n\n\ndef save(p):\n    Path(p).mkdir(parents=True)\n")
    tree = ast.parse(bad.read_text())
    lines = bad.read_text().split("\n")

    mkdir = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "mkdir"
    ]

    assert mkdir, "the probe file does not contain the call the detector looks for"
    assert not _inside_converting_try(tree, mkdir[0].lineno)
    assert not _inside_writing_block(lines, mkdir[0].lineno)


def test_the_enumeration_accepts_both_shapes_of_guard(tmp_path) -> None:
    """A `try/except OSError` and a `with writing(...)` both count.

    Both are in use -- `index.save` predates the shared helper and states its
    own remedy -- and a detector that recognised only one would push the other
    into a needless rewrite.
    """
    guarded_try = tmp_path / "with_try.py"
    guarded_try.write_text(
        "from pathlib import Path\n\n\ndef save(p):\n    try:\n        Path(p).mkdir()\n    except OSError:\n        raise\n"
    )
    tree = ast.parse(guarded_try.read_text())
    mkdir = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "mkdir"
    )
    assert _inside_converting_try(tree, mkdir.lineno)

    guarded_with = tmp_path / "with_writing.py"
    guarded_with.write_text(
        'from pathlib import Path\n\n\ndef save(p):\n    with writing(p, "x", "y"):\n        Path(p).mkdir()\n'
    )
    lines = guarded_with.read_text().split("\n")
    tree2 = ast.parse(guarded_with.read_text())
    mkdir2 = next(
        n
        for n in ast.walk(tree2)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "mkdir"
    )
    assert _inside_writing_block(lines, mkdir2.lineno)


def test_the_tool_actually_makes_writes_worth_guarding() -> None:
    """Guards against the detector silently matching nothing.

    If `WRITE_CALLS` were misspelled, or the source moved, the main test above
    would pass by finding zero sites -- green for the worst possible reason.
    """
    total = 0
    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += sum(
            1 for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", None) in WRITE_CALLS
        )

    assert total >= 8, f"only {total} write calls found in {SOURCE_ROOT}; the detector is looking in the wrong place"


# ---------------------------------------------------------------------------
# The shared rule's own behaviour. ELEVEN SITES NOW DEPEND ON IT, and mutation
# testing caught that nothing exercised it: replacing `except OSError` with
# `except ZeroDivisionError` inside `writing()` left every test above green,
# because they check that the guard is PRESENT, not that it works. A structural
# test and a behavioural test are different tests, and the structural one had
# been quietly doing duty for both.
# ---------------------------------------------------------------------------


def test_writing_converts_an_oserror_into_a_named_vid_error(tmp_path) -> None:
    from vid.core.writes import writing
    from vid.schemas import VidError

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a regular file, so mkdir beneath it genuinely fails")
    destination = blocker / "nested" / "thing.wav"

    with (
        pytest.raises(VidError) as failure,
        writing(destination, "The thing", "Point VID_SOMETHING_DIR at a writable directory."),
    ):
        destination.parent.mkdir(parents=True)

    message = str(failure.value)
    assert "The thing" in message, f"the artefact is not named: {message!r}"
    assert str(destination) in message, f"the destination is not named: {message!r}"
    assert "Point VID_SOMETHING_DIR" in message, f"the caller's remedy is lost: {message!r}"
    assert "Not a directory" in message, f"the real cause is lost: {message!r}"


def test_writing_lets_a_successful_write_through(tmp_path) -> None:
    from vid.core.writes import writing

    destination = tmp_path / "nested" / "thing.wav"
    with writing(destination, "The thing", "remedy"):
        destination.parent.mkdir(parents=True)
        destination.write_text("written")

    assert destination.read_text() == "written"


def test_writing_does_not_swallow_a_non_filesystem_error(tmp_path) -> None:
    """It converts OSError and nothing else.

    A context manager that ate every exception would turn an unrelated bug
    inside a write block into a misleading "could not be written" message.
    """
    from vid.core.writes import writing

    with (
        pytest.raises(ValueError, match="not a filesystem problem"),
        writing(tmp_path / "x", "The thing", "remedy"),
    ):
        raise ValueError("not a filesystem problem")
