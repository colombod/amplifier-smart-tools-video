"""Public overrides must reach real AgentRequest objects, without live providers."""

import importlib.util
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.fixtures import ensure_clips, have_ffmpeg
from tests.test_narrate import FakeSpeaker
from vid import lib
from vid.cli import app
from vid.intelligence.schemas import AgentRequest, AgentResult
from vid.plan import Stitch
from vid.schemas import DEFAULT_INTELLIGENCE_MODEL, VidError


class RecordingIntelligence:
    implementation = "offline-test"

    def __init__(self, replies: list[str]):
        self.replies = iter(replies)
        self.requests: list[AgentRequest] = []

    def preflight(self) -> None:
        pass

    def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        return AgentResult(text=next(self.replies))


@pytest.fixture(params=[{}, {"model": "caller-chosen-model", "reasoning_effort": "high"}])
def options(request):
    return request.param


def assert_options(provider, options, count):
    assert len(provider.requests) == count
    for request in provider.requests:
        assert request.model == options.get("model", DEFAULT_INTELLIGENCE_MODEL)
        assert request.reasoning_effort == options.get("reasoning_effort", "low")


def test_stitch_overrides_reach_selection_and_generation(monkeypatch, options):
    provider = RecordingIntelligence(["NONE\nNo preset fits.", "A*(1-P)+B*P\nA blend."])
    monkeypatch.setattr("vid.intelligence.interface.default_intelligence", lambda: provider)
    monkeypatch.setattr("vid.probe.have_ffmpeg", lambda: True)
    monkeypatch.setattr("vid.transitions.probe", lambda *args: (True, "offline probe boundary"))
    plan = lib.stitch(None, ["a.mp4", "b.mp4"], transition="something very specific", **options)
    assert isinstance(plan.operations[0], Stitch)
    assert plan.operations[0].transition == "custom"
    assert_options(provider, options, 2)
    lib.stitch(None, ["a.mp4", "b.mp4"], transition="fade", **options)
    assert len(provider.requests) == 2


@pytest.mark.parametrize("vision", [False, True])
def test_semantic_and_literal_search_options(monkeypatch, options, vision, capsys):
    record = (
        {
            "shots": [{"id": "s0", "start": 0, "end": 3, "description": "a cat on a mat"}],
        }
        if vision
        else {
            "speech": [{"id": "c0", "start": 0, "end": 3, "text": "a cat on a mat"}],
        }
    )
    provider = RecordingIntelligence([("s0" if vision else "c0") + "\nA relevant passage."])
    monkeypatch.setattr("vid.index.load", lambda _: record)
    monkeypatch.setattr("vid.intelligence.interface.default_intelligence", lambda: provider)
    lib.find("feline resting", "a.mp4", show=True, **options)
    assert_options(provider, options, 1)
    lib.find("cat mat", "a.mp4", show=True, **options)
    assert len(provider.requests) == 1
    assert ("[seen]" if vision else "[literal]") in capsys.readouterr().out


@pytest.mark.skipif(not have_ffmpeg(), reason="vision extracts real frames")
def test_public_vision_index_overrides_and_whisper_size_stay_separate(monkeypatch, tmp_path, options):
    video = str(ensure_clips()["alpha"].path)
    record = {"video": video, "duration": 3, "fingerprint": "offline", "shots": [{"id": "s0", "start": 0, "end": 3}]}
    built = {}

    def build(video, **kwargs):
        built.update(kwargs)
        return record

    provider = RecordingIntelligence(["frame000.jpg: A red frame."])

    # Name the actual frame listed in the real request, not an assumed filename.
    def run(request):
        provider.requests.append(request)
        frame = next(line.split(" -- ")[0] for line in request.prompt.splitlines() if " -- the shot at " in line)
        assert (request.workspace.path / frame).is_file()
        return AgentResult(text=f"{frame}: A red frame.")

    monkeypatch.setattr(provider, "run", run)
    monkeypatch.setattr("vid.intelligence.interface.default_intelligence", lambda: provider)
    monkeypatch.setattr("vid.index.build", build)
    monkeypatch.setattr("vid.index.index_path", lambda _: tmp_path / "index.json")
    # THE SPEECH EXTRA IS DECLARED PRESENT, because this test asserts option
    # PLUMBING (that the whisper size reaches `build` and stays separate from
    # the vision model options) with `build` itself mocked out. `index` now
    # preflights faster-whisper when the stored index has no speech, and this
    # box has no extra installed -- so without this the test would exercise the
    # refusal rather than the plumbing. Mocked-`build`-with-speech-requested
    # cannot occur in real use: there, `build` would transcribe and the backend
    # really would be required.
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: object() if name == "faster_whisper" else real_find_spec(name, *a, **k),
    )
    lib.index(video, vision=True, yes=True, model_size="tiny", **options)
    assert built["model_size"] == "tiny"
    assert_options(provider, options, 1)
    assert json.loads((tmp_path / "index.json").read_text())["shots"][0]["description"] == "A red frame."
    lib.index(video, vision=True, yes=True, **options)
    assert len(provider.requests) == 1, "cached descriptions should not trigger model calls"


def test_public_narration_overrides_reach_script_and_shortening(monkeypatch, tmp_path, options):
    provider = RecordingIntelligence(["0: " + "x" * 80, "x" * 60, "short"])
    monkeypatch.setattr("vid.intelligence.interface.default_intelligence", lambda: provider)
    monkeypatch.setattr("vid.index.load", lambda _: {"video": "a.mp4", "duration": 3, "shots": []})
    monkeypatch.setattr("vid.index.fingerprint", lambda _: "test")
    monkeypatch.setattr("vid.probe.have_ffmpeg", lambda: True)
    monkeypatch.setattr("vid.speech.interface.speech_preflight", lambda _: None)
    monkeypatch.setattr("vid.speech.interface.resolve_speech", lambda _: FakeSpeaker(0.1))
    monkeypatch.setenv("VID_NARRATION_DIR", str(tmp_path / "narration"))

    def assemble(script, out, total):
        Path(out).write_bytes(b"offline assembly boundary")
        return Path(out)

    monkeypatch.setattr("vid.narrate.assemble", assemble)
    lib.narrate("a.mp4", "explain", **options)
    assert_options(provider, options, 3)


@pytest.mark.parametrize(
    ("verb", "args"),
    [
        ("stitch", ["a.mp4", "b.mp4"]),
        ("find", ["a query", "a.mp4"]),
        ("index", ["a.mp4", "--model", "small"]),
        ("narrate", ["a.mp4", "a prompt"]),
    ],
)
def test_cli_model_options_forward_consistently(monkeypatch, verb, args):
    from vid.plan import Plan

    seen = {}

    def facade(*args, **kwargs):
        seen.update(kwargs)
        return Plan(source="a.mp4") if verb == "stitch" else ""

    monkeypatch.setattr(lib, verb, facade)
    result = CliRunner().invoke(app, [verb, *args, "--intelligence-model", "chosen", "--reasoning-effort", "max"])
    assert result.exit_code == 0, result.output
    assert seen["model"] == "chosen"
    assert seen["reasoning_effort"] == "max"
    if verb == "index":
        assert seen["model_size"] == "small"


def test_invalid_reasoning_effort_is_refused_by_cli():
    result = CliRunner().invoke(app, ["find", "query", "a.mp4", "--reasoning-effort", "invented"])
    assert result.exit_code == 2


def test_semantic_vision_refuses_an_invented_shot_id(monkeypatch):
    monkeypatch.setattr(
        "vid.index.load", lambda _: {"shots": [{"id": "s0", "start": 0, "end": 3, "description": "a cat"}]}
    )
    provider = RecordingIntelligence(["s0 s99\nThese shots."])
    monkeypatch.setattr("vid.intelligence.interface.default_intelligence", lambda: provider)
    with pytest.raises(VidError, match="s99"):
        lib.find("feline resting", "a.mp4", show=True)
