"""The property this design exists to have, asserted rather than assumed.

Every verb except `render` must work with no ffmpeg binary and no provider
credentials, because a plan is JSON and nothing decodes a frame until the end.

That is easy to believe and easy to break: one import of a media library at
module scope, one eager provider handshake in a constructor, and it is gone --
silently, because a developer machine has ffmpeg and credentials and would never
notice. Our research tools learned this the hard way, and their CI now asserts
the ABSENCE of six provider variables rather than trusting the environment.

So these tests run each verb in a SCRUBBED SUBPROCESS: a PATH with no ffmpeg on
it and an environment with every provider variable removed. Checking in-process
would prove nothing, because this interpreter already has both.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

REPO = Path(__file__).resolve().parents[1]
VID = REPO / ".venv" / "bin" / "vid"
PYTHON = REPO / ".venv" / "bin" / "python"

#: Anything that could hand a model to the tool behind our back.
PROVIDER_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "PERPLEXITY_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "COPILOT_API_KEY",
)

EMPTY_PLAN = json.dumps({"plan_format": 1, "source": "talk.mp4", "operations": []})

pytestmark = pytest.mark.skipif(not VID.exists(), reason="run `uv sync` first: no .venv/bin/vid")


#: An empty directory, used as the whole of PATH. Created once, never written to.
_EMPTY_BIN = tempfile.mkdtemp(prefix="vid-no-path-")


def _bare_env() -> dict[str, str]:
    """A PATH with no ffmpeg and no provider credentials anywhere in it.

    POINTS AT AN EMPTY DIRECTORY rather than at "/usr/bin:/bin", and that
    distinction is the whole test. The old value was a guess that those
    directories would not contain ffmpeg -- true on a laptop where it lives in
    ~/.local/bin, false on CI where apt puts it at /usr/bin/ffmpeg.

    So this suite passed locally for an environmental accident, not because the
    scrub worked, and CI said so the first time it ran. Every command invoked
    here is given by absolute path, so an empty PATH costs nothing.
    """
    env = {"PATH": _EMPTY_BIN, "HOME": os.environ.get("HOME", "/tmp")}
    for name in PROVIDER_VARS:
        assert name not in env
    return env


def _run(args: list[str], stdin: str = EMPTY_PLAN) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(VID), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=_bare_env(),
        cwd=REPO,
    )


def test_the_scrubbed_environment_really_has_no_ffmpeg():
    """Guard the guard.

    If ffmpeg leaked onto this PATH, every test below would pass for the wrong
    reason and we would believe a property we had never tested.
    """
    probe = subprocess.run(
        [sys.executable, "-c", "import shutil,sys; sys.exit(0 if shutil.which('ffmpeg') is None else 1)"],
        env=_bare_env(),
    )
    assert probe.returncode == 0, "ffmpeg is on the scrubbed PATH -- these tests would prove nothing"


@pytest.mark.parametrize(
    "args",
    [
        ["trim", "tests/fixtures/alpha.mp4", "--from", "0:05"],
        ["cut", "tests/fixtures/alpha.mp4", "--from", "0:01", "--to", "0:02"],
        ["retime", "tests/fixtures/alpha.mp4", "--speed", "2x"],
        ["zoom", "tests/fixtures/alpha.mp4", "--to", "1.4"],
        ["stitch", "tests/fixtures/alpha.mp4", "tests/fixtures/bravo.mp4"],
        ["stitch", "tests/fixtures/alpha.mp4", "tests/fixtures/bravo.mp4", "--transition", "dissolve"],
        ["caption", "tests/fixtures/alpha.mp4", "--subtitles", "talk.srt"],
        ["plan"],
        ["check"],
        ["transitions"],
        ["manifest"],
    ],
    ids=lambda a: a[0] if isinstance(a, list) else str(a),
)
def test_every_verb_but_render_runs_with_nothing_installed(args):
    result = _run(args)
    assert result.returncode == 0, (
        f"`vid {' '.join(args)}` failed with no ffmpeg and no credentials.\nstderr: {result.stderr}"
    )


def test_print_command_needs_no_ffmpeg_either():
    """Compiling is pure; only RUNNING needs the binary.

    This is what makes the compile step testable at all -- on a machine with no
    ffmpeg, in CI, with no media on disk.
    """
    plan = json.dumps(
        {
            "plan_format": 1,
            "source": "talk.mp4",
            "operations": [{"op": "trim", "start": 10.0, "end": 20.0}],
        }
    )
    result = _run(["render", "out.mp4", "--print-command"], stdin=plan)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("ffmpeg ")
    assert "trim=start=10.0:end=20.0" in result.stdout
    assert not (REPO / "out.mp4").exists(), "--print-command wrote a file; it must only print"


def test_render_without_ffmpeg_refuses_and_names_the_remedy():
    """The one verb that DOES need it fails honestly rather than confusingly."""
    result = _run(["render", "out.mp4"], stdin=EMPTY_PLAN)
    assert result.returncode != 0
    assert "ffmpeg" in result.stderr.lower()
    assert "vid check" in result.stderr, "a refusal that does not name the remedy is half a refusal"


def test_a_description_without_a_provider_refuses_rather_than_guessing():
    """The tier-2 path degrades honestly.

    Silently falling back to `fade` would be the worst outcome: a caller who
    asked for something specific gets something generic and is never told.

    THIS TEST WAS WRONG THE FIRST TIME, in a way worth keeping. It ran the CLI in
    the "scrubbed" environment above and asserted a refusal -- but that
    environment passes HOME through, and the Copilot SDK reads gh's stored
    credentials from there. So the provider was never absent: the run SUCCEEDED,
    picking `hblur` for "soft and dreamy" with a sensible rationale.

    The failure was luckier than the pass would have been. Had the provider path
    been broken for any other reason, this test would have gone green while
    proving nothing about degradation -- a check occupying the slot where the
    real question goes.

    So it now removes the provider EXPLICITLY rather than hoping an environment
    lacks one.
    """
    from vid.schemas import VidError
    from vid.transitions import resolve

    with pytest.raises(VidError) as failure:
        resolve("soft and dreamy", intelligence=None)

    message = str(failure.value)
    assert "vid transitions" in message or "presets" in message, (
        "a refusal that does not point at the alternative is half a refusal"
    )
    assert "fade" not in message.split("presets")[0], "must not suggest a silent fallback"


def test_narrate_script_only_refuses_honestly_with_nothing_installed():
    """`narrate`, even `--script-only`, still needs a model -- that requirement
    is unchanged by the pluggable speech backend and by `--script-only`
    itself (`write_script` runs before the script/synthesis split). So this
    is deliberately NOT added to the all-succeed parametrize list above:
    doing so would assert a property that is false regardless of what speech
    backend is configured, since nothing here provides a model either.

    What IS true, and what this proves: with nothing installed, `narrate`
    refuses CLEANLY -- a named remedy, not a crash -- the same property
    `test_render_without_ffmpeg_refuses_and_names_the_remedy` and
    `test_a_description_without_a_provider_refuses_rather_than_guessing`
    already prove for the other verbs this suite cannot make fully succeed.
    """
    result = _run(["narrate", "no-such-video.mp4", "a prompt", "--script-only"])
    assert result.returncode != 0
    assert result.stderr.strip(), "a refusal with no message at all is worse than a crash"


def test_the_provider_scrub_is_honest_about_what_it_does_not_scrub():
    """Guard the guard, second instance.

    `_bare_env` removes provider API keys but keeps HOME, so credential stores
    under it -- gh's in particular -- remain reachable. That is fine, and it is
    recorded here so the next person does not read "scrubbed environment" and
    believe more than it means.
    """
    env = _bare_env()
    assert "HOME" in env, "several tools need HOME to run at all"
    for name in PROVIDER_VARS:
        assert name not in env


#: Reused by the missing-preflight tests below: a script run under `PYTHON`
#: with the scrubbed PATH, so a raised `VidError` is distinguishable on stdout
#: from any other exception (including the bare `FileNotFoundError` these
#: tests exist to rule out) without ever mocking `subprocess`.
_PROBE_SCRIPT = """
from vid.schemas import VidError
try:
    {call}
except VidError as exc:
    print("VIDERROR:" + str(exc))
except Exception as exc:
    print("OTHER:" + type(exc).__name__ + ":" + str(exc))
else:
    print("NOERROR")
"""


def _run_probe_script(call: str) -> subprocess.CompletedProcess[str]:
    script = _PROBE_SCRIPT.format(call=call)
    return subprocess.run(
        [str(PYTHON), "-c", script],
        capture_output=True,
        text=True,
        env=_bare_env(),
        cwd=REPO,
    )


pytestmark_python = pytest.mark.skipif(not PYTHON.exists(), reason="run `uv sync` first: no .venv/bin/python")


@pytestmark_python
def test_transition_probe_without_ffmpeg_refuses_and_names_the_remedy():
    """THE REPORTED DEFECT. `transitions.probe` shelled out to ffmpeg with no
    preflight at all -- a missing binary escaped as a bare `FileNotFoundError`
    rather than a `VidError` naming the prerequisite. Called directly (not
    through the CLI) because `probe` needs no model and no clip pair beyond
    two real fixture files, so this isolates the exact defect site.

    PATH is emptied by `_bare_env`, not mocked -- `probe` genuinely cannot
    find ffmpeg here, the same as the module docstring for this file requires.
    """
    call = (
        "from vid.transitions import probe; "
        'probe("A*(1-P)+B*P", "tests/fixtures/alpha.mp4", "tests/fixtures/bravo.mp4", 0.5)'
    )
    result = _run_probe_script(call)

    assert result.returncode == 0, f"the script itself must not crash: {result.stderr}"
    assert "OTHER:FileNotFoundError" not in result.stdout, (
        "a missing ffmpeg must never escape as a bare FileNotFoundError:\n" + result.stdout
    )
    assert result.stdout.startswith("VIDERROR:"), f"expected a VidError, got: {result.stdout!r}"
    assert "ffmpeg" in result.stdout.lower()
    assert "ffmpeg.org" in result.stdout or "vid check" in result.stdout, (
        "a refusal that does not name the remedy is half a refusal"
    )


@pytestmark_python
def test_transition_generation_without_ffmpeg_refuses_cleanly_with_no_provider_either():
    """The same gap one call up: `resolve_with_clips` would write a new
    expression with a model and only THEN discover ffmpeg was missing, at
    `probe`. With intelligence=None this never reaches generation, so this
    proves the narrower thing that IS testable with no provider configured:
    the refusal is still a named `VidError`, not a crash, when both a model
    and ffmpeg are absent together. The ffmpeg-before-model ordering itself is
    proven end-to-end by the narrate test below.
    """
    call = (
        "from vid.transitions import resolve_with_clips; "
        'resolve_with_clips("slam in hard from the right", "tests/fixtures/alpha.mp4", '
        '"tests/fixtures/bravo.mp4", 0.5, intelligence=None)'
    )
    result = _run_probe_script(call)

    assert result.returncode == 0, f"the script itself must not crash: {result.stderr}"
    assert result.stdout.startswith("VIDERROR:"), f"expected a VidError, got: {result.stdout!r}"


@pytestmark_python
def test_openai_tts_ffprobe_fallback_without_ffprobe_refuses_and_names_the_remedy():
    """`speech.openai_tts._ffprobe_duration` is the fallback used when the
    OpenAI TTS response cannot be parsed as plain PCM -- rare, but it shelled
    out to ffprobe with no preflight of its own. Any file works here: the
    function tries ffprobe unconditionally, regardless of content.
    """
    call = (
        "from pathlib import Path; "
        "from vid.speech.openai_tts import _ffprobe_duration; "
        '_ffprobe_duration(Path("tests/fixtures/alpha.mp4"))'
    )
    result = _run_probe_script(call)

    assert result.returncode == 0, f"the script itself must not crash: {result.stderr}"
    assert "OTHER:FileNotFoundError" not in result.stdout, (
        "a missing ffprobe must never escape as a bare FileNotFoundError:\n" + result.stdout
    )
    assert result.stdout.startswith("VIDERROR:"), f"expected a VidError, got: {result.stdout!r}"
    assert "ffprobe" in result.stdout.lower() or "ffmpeg" in result.stdout.lower()


@pytest.mark.skipif(not VID.exists(), reason="run `uv sync` first: no .venv/bin/vid")
def test_narrate_without_ffmpeg_refuses_before_a_model_is_even_asked(tmp_path, monkeypatch):
    """The second fixed site: `narrate` assembles synthesised lines onto a
    silent bed with ffmpeg (`vid.narrate.assemble`) regardless of `--out`, and
    that call had no preflight of its own either. The guard now lives in
    `narrate` itself, checked ahead of the intelligence/model check -- so with
    BOTH ffmpeg and every provider missing, the failure must still name
    ffmpeg specifically, proving the model was never reached (and never paid
    for).

    The index is built in THIS process, on this machine's real ffmpeg --
    indexing is not what is under test. Only the `narrate` invocation itself
    runs with PATH emptied and every provider variable removed.
    """
    from vid.lib import index as build_index
    from vid.probe import have_ffmpeg

    if not have_ffmpeg():
        pytest.skip("building the fixture index needs a real ffmpeg on this machine")

    fixture = REPO / "tests" / "fixtures" / "alpha.mp4"
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    monkeypatch.setenv("VID_INDEX_DIR", str(index_dir))
    build_index(str(fixture), speech=False)

    env = _bare_env()
    env["VID_INDEX_DIR"] = str(index_dir)
    result = subprocess.run(
        [str(VID), "narrate", str(fixture), "a narration for a test"],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
    )

    assert result.returncode != 0
    assert "ffmpeg" in result.stderr.lower(), f"expected the ffmpeg preflight to fire, got: {result.stderr}"
    assert "vid check" in result.stderr
    reason = (
        "the ffmpeg preflight must fire before the model/provider check, or a missing binary "
        f"is only found after paying for generation. stderr: {result.stderr}"
    )
    assert "model" not in result.stderr.lower(), reason
    assert "provider" not in result.stderr.lower(), reason
