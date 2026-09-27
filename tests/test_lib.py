"""The library, driven directly -- no CLI, no CliRunner.

Every plan-editing and catalogue capability the CLI exposes must be reachable
by a Python caller holding nothing but a `Plan` (or, for `stitch`, a `Plan` and
a list of clips). These tests are the proof: they call `vid.lib` functions
the same way any other Python program would, never `vid.cli`.
"""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tests.fixtures import ensure_clips, have_ffmpeg
from vid import lib
from vid.plan import AudioMix, AudioReplace, Caption, Cut, Grade, Lut, Plan, Retime, Stitch, Trim, Vignette, Zoom
from vid.schemas import VidError

# ---------------------------------------------------------------------------
# trim / cut / retime / zoom / caption / show / vignette / lut -- every one of
# these is pure: no ffmpeg, no provider, no filesystem beyond the Plan itself.
# ---------------------------------------------------------------------------


def test_trim_parses_timecodes_and_builds_the_operation():
    result = lib.trim(Plan(source="a.mp4"), "1:30", "2:00")
    op = result.operations[-1]
    assert isinstance(op, Trim)
    assert (op.start, op.end) == (90.0, 120.0)


def test_trim_with_no_end_runs_to_the_end():
    result = lib.trim(Plan(source="a.mp4"), "0:05")
    op = result.operations[-1]
    assert isinstance(op, Trim)
    assert op.end is None


def test_cut_parses_both_timecodes():
    result = lib.cut(Plan(source="a.mp4"), "10", "20")
    op = result.operations[-1]
    assert isinstance(op, Cut)
    assert (op.start, op.end) == (10.0, 20.0)


def test_retime_needs_exactly_one_of_speed_or_ramp():
    with pytest.raises(VidError, match="exactly one"):
        lib.retime(Plan(source="a.mp4"))
    with pytest.raises(VidError, match="exactly one"):
        lib.retime(Plan(source="a.mp4"), speed="2x", ramp="1x@0 0.5x@1")


def test_retime_builds_a_constant_speed_operation():
    result = lib.retime(Plan(source="a.mp4"), speed="2x")
    op = result.operations[-1]
    assert isinstance(op, Retime)
    assert op.speed == 2.0


def test_retime_builds_ramp_control_points():
    result = lib.retime(Plan(source="a.mp4"), ramp="1x@0 0.25x@1:05 1x@1:12")
    op = result.operations[-1]
    assert isinstance(op, Retime)
    assert [p.speed for p in op.ramp] == [1.0, 0.25, 1.0]
    assert [p.at for p in op.ramp] == [0.0, 65.0, 72.0]


def test_retime_ramp_rejects_a_token_with_no_at():
    with pytest.raises(VidError, match="ramp point"):
        lib.retime(Plan(source="a.mp4"), ramp="2x")


def test_zoom_defaults_and_a_named_moment():
    result = lib.zoom(Plan(source="a.mp4"), at="0:05")
    op = result.operations[-1]
    assert isinstance(op, Zoom)
    assert (op.to, op.at, op.duration) == (1.3, 5.0, 3.0)


def test_caption_builds_the_operation():
    result = lib.caption(Plan(source="a.mp4"), "subs.srt", style="FontSize=28")
    op = result.operations[-1]
    assert isinstance(op, Caption)
    assert (op.subtitles, op.style) == ("subs.srt", "FontSize=28")


def test_show_is_the_identity():
    plan = Plan(source="a.mp4")
    assert lib.show(plan) is plan


def test_vignette_builds_the_operation():
    result = lib.vignette(Plan(source="a.mp4"), 0.5)
    op = result.operations[-1]
    assert isinstance(op, Vignette)
    assert op.strength == 0.5


def test_lut_builds_the_operation():
    result = lib.lut(Plan(source="a.mp4"), "table.cube")
    op = result.operations[-1]
    assert isinstance(op, Lut)
    assert op.path == "table.cube"


def test_audio_remove_replace_mix_are_reachable_with_no_cli():
    plan = Plan(source="a.mp4")
    removed = lib.audio_remove(plan).operations[-1]
    assert removed.op == "audio_remove"

    replaced = lib.audio_replace(plan, "b.wav").operations[-1]
    assert isinstance(replaced, AudioReplace)
    assert replaced.track == "b.wav"

    mixed = lib.audio_mix(plan, "b.wav", level=-6.0).operations[-1]
    assert isinstance(mixed, AudioMix)
    assert (mixed.track, mixed.level) == ("b.wav", -6.0)


# ---------------------------------------------------------------------------
# stitch -- both the piped-plan shape (an existing Plan) and the explicit
# shape (None, starting fresh from the first source).
# ---------------------------------------------------------------------------


def test_stitch_continues_an_existing_plan_with_a_named_preset():
    plan = Plan(source="a.mp4")
    result = lib.stitch(plan, ["b.mp4", "c.mp4"], transition="dissolve")
    op = result.operations[-1]
    assert isinstance(op, Stitch)
    assert result.source == "a.mp4"
    assert (op.sources, op.transition) == (["b.mp4", "c.mp4"], "dissolve")
    assert op.transition_verified is False, "a preset needs no proving"


def test_stitch_with_no_plan_starts_fresh_from_the_first_source():
    result = lib.stitch(None, ["a.mp4", "b.mp4"])
    op = result.operations[-1]
    assert isinstance(op, Stitch)
    assert result.source == "a.mp4"
    assert op.sources == ["b.mp4"]


def test_stitch_needs_at_least_one_clip_to_join_on():
    with pytest.raises(VidError, match="at least one clip"):
        lib.stitch(Plan(source="a.mp4"), [])
    with pytest.raises(VidError, match="at least one clip"):
        lib.stitch(None, [])


# ---------------------------------------------------------------------------
# recolor -- needs the plan to already carry a source.
# ---------------------------------------------------------------------------


def test_recolor_needs_a_plan_with_a_source():
    with pytest.raises(VidError, match="source video"):
        lib.recolor(Plan(), "reference.png", 1.0)


# ---------------------------------------------------------------------------
# Deviations "failure-names-remedy" / "prerequisite-failures-match-manifest":
# recolor samples frames to measure a palette while BUILDING the plan, unlike
# every other plan-building capability, so a missing ffmpeg used to escape as
# a bare `FileNotFoundError` from inside `subprocess.run` -- a traceback, not
# a named failure a caller (usually an agent) could act on.
# ---------------------------------------------------------------------------


def test_recolor_with_no_ffmpeg_names_the_manifests_install_reference_not_a_traceback(monkeypatch, tmp_path):
    """A real, empty PATH -- not a stubbed `shutil.which` -- so this proves the
    actual preflight code path, not an assumption about how it calls `which`."""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"stand-in bytes -- the ffmpeg preflight must fire before this is ever read")

    with pytest.raises(VidError) as failure:
        lib.recolor_op("source.mp4", str(reference))

    message = str(failure.value)
    assert "ffmpeg" in message.lower()
    assert "https://ffmpeg.org/download.html" in message, "must cite the manifest's own ffmpeg install reference"


# ---------------------------------------------------------------------------
# grade -- the catalogue, the missing-look refusal, and applying a look, all
# through one library function returning either text or a Plan.
# ---------------------------------------------------------------------------


def test_grade_list_returns_the_catalogue_with_no_plan_needed():
    text = lib.grade(None, show=True)
    assert isinstance(text, str)
    assert "warm" in text


def test_grade_without_a_look_names_the_remedy():
    with pytest.raises(VidError, match="--list"):
        lib.grade(Plan(source="a.mp4"), None)


def test_grade_applies_the_named_look():
    result = lib.grade(Plan(source="a.mp4"), "warm")
    assert isinstance(result, Plan)
    op = result.operations[-1]
    assert isinstance(op, Grade)
    assert op.look == "warm"


# ---------------------------------------------------------------------------
# transitions -- the bare list and the described catalogue, both from lib.
# ---------------------------------------------------------------------------


def test_transitions_bare_lists_every_preset_name():
    names = lib.transitions().split()
    assert "dissolve" in names
    assert "fade" in names


def test_transitions_describe_explains_each_one():
    text = lib.transitions(describe=True)
    assert "soft and dreamy" in text
    assert "dissolve" in text


# ---------------------------------------------------------------------------
# Deviation 10 (half): indexing preflights ffmpeg/ffprobe before any work,
# rather than failing late with a bare FileNotFoundError.
# ---------------------------------------------------------------------------


def test_index_refuses_immediately_with_no_ffmpeg_on_path(monkeypatch, tmp_path):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    with pytest.raises(VidError) as failure:
        lib.index("whatever.mp4")

    message = str(failure.value)
    assert "ffmpeg" in message.lower() or "ffprobe" in message.lower()
    assert "vid check" in message


# ---------------------------------------------------------------------------
# Deviation "prerequisite-failures-match-manifest": `index --vision` used to
# build (and write) the whole index before ever checking whether a model
# provider was even reachable. A missing provider must be caught BEFORE
# `build()` runs, so it leaves no new index state behind.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not have_ffmpeg(), reason="needs real ffmpeg/ffprobe present to reach the provider check")
def test_index_vision_with_no_provider_fails_before_any_index_file_is_written(monkeypatch, tmp_path):
    monkeypatch.setenv("VID_INDEX_DIR", str(tmp_path / "index-store"))
    video = str(tmp_path / "clip.mp4")
    Path(video).write_bytes(b"stand-in bytes -- ffmpeg must never be asked to decode this if the fix holds")

    class _NoProvider:
        def preflight(self) -> None:
            raise VidError("no provider configured, for this test")

    monkeypatch.setattr("vid.intelligence.interface.default_intelligence", lambda: _NoProvider())

    from vid.index import index_path

    # MATCHES THE PROVIDER'S OWN WORDS, not the word "model". This used to
    # assert `match="model"`, which pinned the GENERIC "needs a model, and none
    # is configured" message -- and that message was the defect: it replaced
    # the provider's precise diagnosis (gh missing, gh not signed in, with the
    # manifest's install reference) with advice to configure something the
    # caller had already configured. The refusal now carries the real reason
    # through, so this asserts the reason survives.
    with pytest.raises(VidError, match="no provider configured, for this test"):
        lib.index(video, vision=True)

    assert not index_path(video).is_file(), "a missing provider left a new index file behind on disk"


# ---------------------------------------------------------------------------
# Deviation 6: the vision-indexing prompt is a diagnostic and belongs on
# stderr; only the caller's answer touches stdin. Tested at the level of the
# helper that owns this, with both output streams captured separately.
# ---------------------------------------------------------------------------


class _FakeStdin:
    def __init__(self, tty: bool, answer: str = "") -> None:
        self._tty = tty
        self._answer = answer

    def isatty(self) -> bool:
        return self._tty

    def readline(self) -> str:
        return self._answer


def test_confirm_before_spending_prompts_on_stderr_and_answers_from_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True, answer="y\n"))

    result = lib._confirm_before_spending("2 shot(s) to describe.", yes=False)

    captured = capsys.readouterr()
    assert result is True
    assert captured.out == "", "the prompt leaked into stdout, where the machine-readable result goes"
    assert "Describe them?" in captured.err
    assert "2 shot(s) to describe." in captured.err


def test_confirm_before_spending_declines_on_a_no_answer(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True, answer="n\n"))
    assert lib._confirm_before_spending("notice", yes=False) is False


def test_confirm_before_spending_refuses_when_not_interactive_and_not_yes(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
    with pytest.raises(VidError, match="Re-run with --yes"):
        lib._confirm_before_spending("notice", yes=False)


def test_confirm_before_spending_skips_the_prompt_entirely_when_yes(monkeypatch, capsys):
    # No stdin patch: if this touched stdin at all with --yes already given,
    # it would hang or raise in a non-interactive test run.
    assert lib._confirm_before_spending("notice", yes=True) is True
    assert capsys.readouterr() == ("", "")


# ---------------------------------------------------------------------------
# Deviation 10 (half): narrate() must not spend a model call before checking
# whether the synthesiser it will need next is even installed.
# ---------------------------------------------------------------------------


class _FakeIntelligence:
    def preflight(self) -> None:
        return None


def _patch_narrate_prerequisites(monkeypatch, *, piper_available: bool) -> None:
    monkeypatch.setattr("vid.index.load", lambda video: {"video": video, "duration": 10.0, "shots": [], "speech": []})
    monkeypatch.setattr("vid.voice.available", lambda: piper_available)
    monkeypatch.setattr("vid.intelligence.interface.default_intelligence", lambda: _FakeIntelligence())


def test_narrate_checks_piper_before_the_first_model_call(monkeypatch):
    _patch_narrate_prerequisites(monkeypatch, piper_available=False)
    calls = []
    monkeypatch.setattr("vid.narrate.write_script", lambda record, prompt, intelligence, **kwargs: calls.append(1))

    with pytest.raises(VidError) as failure:
        lib.narrate("video.mp4", "a prompt")

    assert not calls, "the model was called even though Piper (checked first) is missing"
    assert "vid[voice]" in str(failure.value) or "speech synthesiser" in str(failure.value).lower()


def test_narrate_script_only_never_needs_piper(monkeypatch):
    _patch_narrate_prerequisites(monkeypatch, piper_available=False)
    fake_script = SimpleNamespace(to_json=lambda: '{"lines": []}')
    monkeypatch.setattr("vid.narrate.write_script", lambda record, prompt, intelligence, **kwargs: fake_script)

    result = lib.narrate("video.mp4", "a prompt", script_only=True)

    assert result == '{"lines": []}'


# ---------------------------------------------------------------------------
# Deviation 7: narration intermediates live in a directory this cleans up,
# and only the final track is copied out to a durable, documented location.
# ---------------------------------------------------------------------------


def test_narrate_cleans_up_intermediates_and_persists_only_the_track(monkeypatch, tmp_path):
    durable_root = tmp_path / "narration-store"
    monkeypatch.setenv("VID_NARRATION_DIR", str(durable_root))
    _patch_narrate_prerequisites(monkeypatch, piper_available=True)
    monkeypatch.setattr("vid.index.fingerprint", lambda video: "deadbeef")
    monkeypatch.setattr("vid.voice.Speaker", lambda name: object())

    fake_script = SimpleNamespace(lines=[], unfitted=list)
    monkeypatch.setattr("vid.narrate.write_script", lambda record, prompt, intelligence, **kwargs: fake_script)

    seen_workdir: dict[str, Path] = {}

    def fake_fit(script, speaker, workdir, intelligence, **kwargs):
        seen_workdir["path"] = Path(workdir)
        assert seen_workdir["path"].is_dir(), "fit must receive a real, existing scoped directory"
        return script

    def fake_assemble(script, out, total):
        Path(out).write_bytes(b"RIFF....fake wav data")
        return Path(out)

    monkeypatch.setattr("vid.narrate.fit", fake_fit)
    monkeypatch.setattr("vid.narrate.assemble", fake_assemble)

    report = lib.narrate("video.mp4", "a prompt")

    assert not seen_workdir["path"].exists(), "the scoped temp directory must be cleaned up on return"

    durable_track = durable_root / "deadbeef.wav"
    assert durable_track.is_file(), "the narration track must survive at the documented durable location"
    assert str(durable_track) in report


# ---------------------------------------------------------------------------
# Deviation "artifact-location-named": every capability that writes an
# artifact to a caller-supplied path must resolve it to an ABSOLUTE path
# before writing, and report that same resolved path back -- a relative
# `output` is not resolvable by a caller who reads the report from a
# different working directory than the one the command ran in.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not have_ffmpeg(), reason="renders a real video")
def test_render_with_a_relative_output_path_returns_an_absolute_one_that_was_actually_written(monkeypatch, tmp_path):
    clip = ensure_clips()["alpha"]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = lib.render(Plan(source=str(clip.path)), "out.mp4")

    assert Path(result).is_absolute(), f"render returned a non-absolute path: {result!r}"
    assert result == str(elsewhere / "out.mp4")
    assert Path(result).is_file(), "the returned path does not point at the file actually written"


@pytest.mark.skipif(not have_ffmpeg(), reason="extracts real audio")
def test_audio_extract_with_a_relative_output_path_returns_an_absolute_one_that_was_actually_written(
    monkeypatch, tmp_path
):
    clip = ensure_clips()["alpha"]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    report = lib.audio_extract(str(clip.path), "sound.wav")

    written = report.removeprefix("wrote ")
    assert Path(written).is_absolute(), f"audio_extract reported a non-absolute path: {written!r}"
    assert written == str(elsewhere / "sound.wav")
    assert Path(written).is_file(), "the reported path does not point at the file actually written"


@pytest.mark.skipif(not have_ffmpeg(), reason="renders real audio and video")
def test_narrate_with_a_relative_out_path_returns_an_absolute_one_that_was_actually_written(monkeypatch, tmp_path):
    clip = ensure_clips()["alpha"]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    _patch_narrate_prerequisites(monkeypatch, piper_available=True)
    monkeypatch.setattr("vid.index.load", lambda video: {"video": video, "duration": 3.0, "shots": [], "speech": []})
    monkeypatch.setattr("vid.voice.Speaker", lambda name: object())

    fake_script = SimpleNamespace(lines=[], unfitted=list)
    monkeypatch.setattr("vid.narrate.write_script", lambda record, prompt, intelligence, **kwargs: fake_script)
    monkeypatch.setattr("vid.narrate.fit", lambda script, speaker, workdir, intelligence, **kwargs: script)

    def fake_assemble(script, out, total):
        # A REAL, decodable silent bed -- built with ffmpeg, not hand-rolled
        # bytes -- so the render step immediately below actually runs ffmpeg
        # for real and the resolved path is proved against a genuine file.
        import subprocess

        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={total}", str(out)],
            check=True,
            capture_output=True,
        )
        return Path(out)

    monkeypatch.setattr("vid.narrate.assemble", fake_assemble)

    report = lib.narrate(str(clip.path), "a prompt", out="narrated.mp4")

    resolved = elsewhere / "narrated.mp4"
    assert str(resolved) in report, f"the report did not name the resolved absolute path:\n{report}"


# ---------------------------------------------------------------------------
# Deviation "artifact-location-named": a relative VID_NARRATION_DIR used to be
# handed straight to callers, who could only resolve the reported track path
# correctly from the one cwd this process happened to run in.
# ---------------------------------------------------------------------------


def test_narration_dir_resolves_a_relative_override_to_an_absolute_path(monkeypatch, tmp_path):
    workdir = tmp_path / "elsewhere"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("VID_NARRATION_DIR", "relative-narration-store")

    resolved = lib._narration_dir()

    assert resolved.is_absolute()
    assert resolved == (workdir / "relative-narration-store").resolve()


# ---------------------------------------------------------------------------
# Deviation "failure-names-remedy": a described-tier miss used to say only
# "Nothing in <video> matches <query>." with no next action. `find` searches
# speech before vision, and the described (model) tier is handed speech
# chunks in preference to vision chunks whenever both exist -- see
# `vid.find.find`'s docstring -- so the remedy must name which side was
# actually searched, not a canned line.
# ---------------------------------------------------------------------------


class _NoneIntelligence:
    """Replies NONE to every described-tier request -- a real miss, not an error."""

    def preflight(self) -> None:
        return None

    def run(self, request):
        from vid.intelligence.schemas import AgentResult

        return AgentResult(text="NONE\nnothing here matches that", error=None)


def test_find_with_speech_but_no_vision_says_search_covered_said_not_shown(monkeypatch):
    monkeypatch.setattr(
        "vid.index.load",
        lambda video: {
            "video": video,
            "duration": 10.0,
            "speech": [{"id": "c0", "start": 0.0, "end": 5.0, "text": "hello world this is a test"}],
            "shots": [],
        },
    )
    monkeypatch.setattr("vid.intelligence.interface.default_intelligence", lambda: _NoneIntelligence())

    with pytest.raises(VidError) as failure:
        lib.find("pricing plan", "video.mp4")

    message = str(failure.value)
    assert "SAID" in message
    assert "SHOWN" in message
    assert "--vision" in message, f"no remedy pointing at vision indexing:\n{message}"


def test_find_with_vision_but_no_speech_index_says_search_covered_shown_not_said(monkeypatch):
    monkeypatch.setattr(
        "vid.index.load",
        lambda video: {
            "video": video,
            "duration": 10.0,
            "shots": [{"id": "s0", "start": 0.0, "end": 5.0, "description": "a cat sitting on a mat"}],
            # no "speech" key at all: this video was never indexed for speech.
        },
    )
    monkeypatch.setattr("vid.intelligence.interface.default_intelligence", lambda: _NoneIntelligence())

    with pytest.raises(VidError) as failure:
        lib.find("pricing plan", "video.mp4")

    message = str(failure.value)
    assert "SHOWN" in message
    assert "SAID" in message
    assert "`vid index video.mp4`" in message, f"no remedy pointing at speech indexing:\n{message}"
    assert "--vision" not in message, f"told the caller to index vision, which this video already has:\n{message}"
