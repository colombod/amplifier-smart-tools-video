"""PR39 timing checks at decoded-picture and public-command boundaries."""

from __future__ import annotations

from collections import Counter
import itertools
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from tests.fixtures import ensure_clips, ensure_long_base_clip, ensure_silent_clip, have_ffmpeg
from tests.test_timing_verification_v2 import Property, measure
from tests.test_timing_verification_v2 import labels as graph_labels
from vid.compile import Compiler, compile_plan
from vid.plan import (
    AudioMix,
    AudioRemove,
    AudioReplace,
    Cut,
    LayerAudio,
    Overlay,
    Plan,
    RampPoint,
    Retime,
    Stitch,
    Trim,
    Zoom,
)
from vid.probe import video_duration
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="renders and decodes real media")


@pytest.mark.parametrize(
    "operation",
    [
        Trim(start=0, end=1),
        Cut(start=0.2, end=0.4),
        Stitch(sources=["b.mp4"]),
        Stitch(sources=["b.mp4"], transition="fade", transition_duration=0.2),
    ],
)
def test_grid_clear_after_edit(operation: Trim | Cut | Stitch) -> None:
    compiler = Compiler(
        Plan(source="a.mp4"), durations={"a.mp4": 3, "b.mp4": 3}, frame_rate=30, source_audio={"b.mp4": True}
    )
    compiler.retime(Retime(speed=1))
    assert compiler.uniform_grid, "[grid-state] retime did not establish grid"
    getattr(compiler, operation.op)(operation)
    assert not compiler.uniform_grid, "[grid-state] edit left stale grid proof"


@pytest.mark.parametrize(
    "operation", [Retime(speed=2), Retime(ramp=[RampPoint(at=0, speed=1), RampPoint(at=3, speed=2)]), Zoom(to=1)]
)
@pytest.mark.parametrize("rate", [None, 30])
def test_grid_established_only_by_regrid(operation: Retime | Zoom, rate: float | None) -> None:
    compiler = Compiler(
        Plan(source="a.mp4"), durations={"a.mp4": 3}, frame_rate=rate, dimensions=(320, 240), has_audio=False
    )
    assert not compiler.uniform_grid, "[grid-state] source nominal rate is not a grid"
    if isinstance(operation, Zoom) and rate is None:
        return
    getattr(compiler, operation.op)(operation)
    assert compiler.uniform_grid == bool(rate), "[grid-state] missing or invented regrid proof"


def run(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, f"[render-refused] {result.stderr}"
    return result


def printed_render(plan: Plan, output: Path) -> list[str]:
    printed = run([sys.executable, "-m", "vid.cli", "render", str(output), "--print-command"], plan.model_dump_json())
    assert not output.exists()
    assert "note:" not in printed.stderr
    argv = shlex.split(printed.stdout)
    run(argv)
    return argv


def assert_single_consumers(argv: list[str]) -> None:
    graph = argv[argv.index("-filter_complex") + 1]
    entries = [graph_labels(entry) for entry in graph.split(";")]
    outputs = [label for _, labels in entries for label in labels]
    consumers = Counter(label for labels, _ in entries for label in labels)
    consumers.update(value.strip("[]") for i, value in enumerate(argv) if i and argv[i - 1] == "-map")
    assert len(outputs) == len(set(outputs)), f"[graph-structure] duplicate outputs: {outputs}"
    assert {label: consumers[label] for label in outputs} == dict.fromkeys(outputs, 1), (
        "[graph-structure] output consumers"
    )


def measured_picture(output: Path, count: int, audio: bool = True) -> float:
    data = json.loads(
        run(["ffprobe", "-v", "error", "-show_streams", "-show_frames", "-of", "json", str(output)]).stdout
    )
    streams = {stream["codec_type"]: stream for stream in data["streams"]}
    pts = [float(frame["best_effort_timestamp_time"]) for frame in data["frames"] if frame["media_type"] == "video"]
    assert len(pts) == count
    assert pts[0] == 0
    assert max(b - a for a, b in itertools.pairwise(pts)) <= 1 / 30 + 1e-6
    end = pts[-1] + 1 / 30
    assert end == pytest.approx(count / 30, abs=1e-6)
    assert float(streams["video"]["duration"]) == pytest.approx(end, abs=1e-6)
    assert ("audio" in streams) == audio
    if audio:
        assert float(streams["audio"]["duration"]) == pytest.approx(
            end, abs=1024 / int(streams["audio"]["sample_rate"])
        )
    return end


@pytest.fixture(scope="module")
def long_audio(tmp_path_factory: pytest.TempPathFactory) -> str:
    clips = ensure_clips()
    output = tmp_path_factory.mktemp("long-audio") / "long-audio.mp4"
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(clips["alpha"].path),
            "-stream_loop",
            "1",
            "-i",
            str(clips["bravo"].path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            str(output),
        ]
    )
    data = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)]).stdout)
    streams = {stream["codec_type"]: stream for stream in data["streams"]}
    assert float(streams["video"]["duration"]) == 3
    assert float(streams["audio"]["duration"]) == pytest.approx(6, abs=0.05)
    return str(output)


@pytest.mark.parametrize("position", ["first", "last", "unequal"])
@pytest.mark.parametrize("consumer", ["keep", "replace", "mix", "overlay"])
def test_stitch_audio_is_fitted_to_each_picture(
    long_audio: str, position: str, consumer: str, tmp_path: Path, record_property: Property
) -> None:
    track = str(ensure_clips()["bravo"].path.resolve())
    base = str(ensure_long_base_clip().path.resolve()) if position == "unequal" else track
    source, incoming = (long_audio, base) if position == "first" else (base, long_audio)
    plan = Plan(source=source).with_operation(Stitch(sources=[incoming]))
    if consumer == "replace":
        plan = plan.with_operation(AudioReplace(track=track))
    elif consumer == "mix":
        plan = plan.with_operation(AudioMix(track=track))
    elif consumer == "overlay":
        plan = plan.with_operation(Overlay(source=track, audio=LayerAudio(policy="keep")))
    output = tmp_path / "stitch.mp4"
    diagnostic = measure(plan, output, record_property)
    diagnostic.audio()
    expected = 9 if position == "unequal" else 6
    end = measured_picture(output, expected * 30)
    compiler = Compiler(plan, durations={source: video_duration(source), incoming: video_duration(incoming)})
    assert isinstance(plan.operations[0], Stitch)
    compiler.stitch(plan.operations[0])
    assert compiler.elapsed == pytest.approx(end, abs=1e-6)


@pytest.mark.parametrize(("lo", "hi", "count"), [(0.5, 2, 83), (2, 2, 45), (4, 4, 23), (0.25, 3.25, 74)])
@pytest.mark.parametrize("sound", ["retained", "removed", "natural"])
@pytest.mark.parametrize("consumer", ["replace", "overlay"])
def test_ramp_has_one_picture_clock(
    lo: float, hi: float, count: int, sound: str, consumer: str, tmp_path: Path, record_property: Property
) -> None:
    clips = ensure_clips()
    source = str((ensure_silent_clip() if sound == "natural" else clips["alpha"]).path.resolve())
    track = str(clips["bravo"].path.resolve())
    plan = Plan(source=source)
    if sound == "removed":
        plan = plan.with_operation(AudioRemove())
    ramp = Retime(ramp=[RampPoint(at=0, speed=lo), RampPoint(at=3, speed=hi)])
    plan = plan.with_operation(ramp)
    base = measure(plan, tmp_path / "base.mp4", record_property) if sound == "natural" else None
    plan = plan.with_operation(
        AudioReplace(track=track) if consumer == "replace" else Overlay(source=track, audio=LayerAudio(policy="keep"))
    )
    output = tmp_path / "ramp.mp4"
    diagnostic = measure(plan, output, record_property)
    diagnostic.audio()
    if base is not None:
        base.picture()
        diagnostic.picture()
        assert diagnostic.frames == base.frames, "[picture-count] consumer changed natural ramp cardinality"
        assert diagnostic.pts == base.pts, "[picture-gap] consumer changed natural ramp timestamps"
        return
    end = measured_picture(output, count)
    compiler = Compiler(
        Plan(source=source), durations={source: video_duration(source)}, has_audio=sound == "retained", frame_rate=30
    )
    compiler.retime(ramp)
    assert compiler.elapsed == pytest.approx(end, abs=1e-6)


@pytest.mark.parametrize(("speed", "count"), [(2, 45), (4, 23)])
def test_flat_ramp_is_frame_identical_to_constant(speed: float, count: int, tmp_path: Path) -> None:
    source = str(ensure_clips()["alpha"].path.resolve())
    outputs = []
    for name, operation in [
        ("constant", Retime(speed=speed)),
        ("ramp", Retime(ramp=[RampPoint(at=0, speed=speed), RampPoint(at=3, speed=speed)])),
    ]:
        output = tmp_path / f"{name}.mp4"
        plan = Plan(source=source).with_operation(AudioRemove()).with_operation(operation)
        printed_render(plan, output)
        measured_picture(output, count, audio=False)
        hashes = run(["ffmpeg", "-v", "error", "-i", str(output), "-map", "0:v", "-f", "framemd5", "-"]).stdout
        outputs.append([line for line in hashes.splitlines() if not line.startswith("#")])
    assert outputs[0] == outputs[1], "[framemd5] constant and flat ramp decoded frames differ"


def test_trim_then_ramp_has_single_consumers_and_renders(tmp_path: Path) -> None:
    source = str(ensure_clips()["alpha"].path.resolve())
    plan = (
        Plan(source=source)
        .with_operation(Trim(start=0.5, end=2.5))
        .with_operation(Retime(ramp=[RampPoint(at=0, speed=0.5), RampPoint(at=2, speed=2)]))
    )
    output = tmp_path / "trim-ramp.mp4"
    printed_render(plan, output)
    # Fixture count supplied by the verification specification; no production oracle.
    measured_picture(output, 55)
    assert_single_consumers(printed_render(plan, tmp_path / "trim-ramp-shape.mp4"))


@pytest.mark.parametrize("pure", [False, True])
def test_audio_on_picture_unknown_does_not_borrow_elapsed(pure: bool) -> None:
    compiler = Compiler(Plan(source="known.mp4"), durations={"known.mp4": 6}, pure_compile=pure)
    if not pure:
        with pytest.raises(VidError, match=r"known positive.*duration"):
            compiler._audio_on_picture("1:a", None, "stitch", "incoming audio length bound", "incoming clip duration")
    else:
        label = compiler._audio_on_picture(
            "1:a", None, "stitch", "incoming audio length bound", "incoming clip duration"
        )
        assert compiler.filters == [f"[1:a]aresample=async=1:first_pts=0[{label}]"]
        assert compiler.omissions == [
            "stitch: apad,atrim,asetpts (incoming audio length bound, needs incoming clip duration)"
        ]


@pytest.mark.parametrize("length", [0, -1, float("nan"), float("inf")])
def test_audio_on_picture_invalid_lengths_are_not_bounds(length: float) -> None:
    compiler = Compiler(Plan(source="known.mp4"), durations={"known.mp4": 6})
    with pytest.raises(VidError, match=r"known positive.*duration"):
        compiler._audio_on_picture("1:a", length, "stitch", "incoming audio length bound", "incoming clip duration")


def test_one_segment_ramp_audio_needs_no_split(tmp_path: Path) -> None:
    source = str(ensure_clips()["alpha"].path.resolve())
    plan = Plan(source=source).with_operation(Retime(ramp=[RampPoint(at=0, speed=2), RampPoint(at=0.4, speed=2)]))
    output = tmp_path / "one-segment.mp4"
    argv = printed_render(plan, output)
    graph = argv[argv.index("-filter_complex") + 1]
    assert "asplit" not in graph, "[graph-structure] unnecessary split"
    assert "concat" not in graph, "[graph-structure] unnecessary concat"
    measured_picture(output, 6)


def test_ramp_audio_finishes_with_picture_bound(tmp_path: Path) -> None:
    source = str(ensure_clips()["alpha"].path.resolve())
    plan = Plan(source=source).with_operation(Retime(ramp=[RampPoint(at=0, speed=0.5), RampPoint(at=3, speed=2)]))
    output = tmp_path / "retained.mp4"
    printed_render(plan, output)
    measured_picture(output, 83)


def test_ramp_audio_graph_shape(tmp_path: Path) -> None:
    source = str(ensure_clips()["alpha"].path.resolve())
    plan = Plan(source=source).with_operation(Retime(ramp=[RampPoint(at=0, speed=0.5), RampPoint(at=3, speed=2)]))
    argv = printed_render(plan, tmp_path / "shape.mp4")
    graph = argv[argv.index("-filter_complex") + 1]
    assert graph.count("fps=30") == 1, "[graph-structure] ramp regrid count"
    assert "apad,atrim=end=2.766667,asetpts=PTS-STARTPTS" in graph, "[graph-structure] ramp bound"


def test_unknown_ramp_retains_nominal_picture_bound() -> None:
    compiler = Compiler(Plan(source="unknown.mp4"), pure_compile=True, has_audio=False)
    compiler.retime(Retime(ramp=[RampPoint(at=1, speed=2), RampPoint(at=2, speed=2)]))
    assert compiler.elapsed is None
    assert compiler.elapsed_cap == 0.5
    assert "trim=start=1:end=2,setpts=PTS-STARTPTS" in ";".join(compiler.filters)
    assert "trim=end=0.500000,setpts=PTS-STARTPTS" in ";".join(compiler.filters)
    assert compiler.omissions == ["retime: fps (frame-rate regrid, needs source frame rate)"]
    assert compiler.assumptions == [
        "retime: sufficient material exists for the 0.5s nominal edit (completed ramp picture length bound)"
    ]


def test_ramp_that_rounds_to_zero_frames_refuses() -> None:
    compiler = Compiler(Plan(source="known.mp4"), durations={"known.mp4": 0.01}, has_audio=False, frame_rate=30)
    with pytest.raises(VidError, match="frame"):
        compiler.retime(Retime(ramp=[RampPoint(at=0, speed=100), RampPoint(at=0.01, speed=100)]))


@pytest.mark.parametrize("position", ["first", "last"])
def test_transition_bounds_both_audio_sides(long_audio: str, position: str, tmp_path: Path) -> None:
    track = str(ensure_clips()["bravo"].path.resolve())
    source, incoming = (long_audio, track) if position == "first" else (track, long_audio)
    output = tmp_path / "transition.mp4"
    plan = Plan(source=source).with_operation(Stitch(sources=[incoming], transition="fade", transition_duration=0.5))
    printed_render(plan, output)
    measured_picture(output, 165)


def test_transition_graph_shape(long_audio: str, tmp_path: Path) -> None:
    track = str(ensure_clips()["bravo"].path.resolve())
    plan = Plan(source=long_audio).with_operation(Stitch(sources=[track], transition="fade", transition_duration=0.5))
    argv = printed_render(plan, tmp_path / "transition-shape.mp4")
    graph = argv[argv.index("-filter_complex") + 1]
    assert graph.count("aresample=async=1:first_pts=0,apad,atrim=end=3.000000,asetpts=PTS-STARTPTS") == 2, (
        "[graph-structure] transition bounds"
    )


def test_stitch_fallback_names_both_independent_bounds() -> None:
    omissions: list[str] = []
    argv = compile_plan(
        Plan(source="a.mp4").with_operation(Stitch(sources=["b.mp4"])),
        "out.mp4",
        pure_compile=True,
        omissions=omissions,
    )
    assert "apad" not in argv[argv.index("-filter_complex") + 1]
    assert omissions == [
        "stitch: setsar (sample aspect normalization, needs clip sizes)",
        "stitch: apad,atrim,asetpts (running audio length bound, needs edit length)",
        "stitch: apad,atrim,asetpts (incoming audio length bound, needs incoming clip duration)",
    ]


def test_constant_tie_elapsed_is_frame_rounded() -> None:
    compiler = Compiler(Plan(source="known.mp4"), durations={"known.mp4": 3}, frame_rate=30)
    compiler.retime(Retime(speed=4))
    assert compiler.elapsed == compiler.elapsed_cap == 23 / 30
    assert "atrim=end=0.766667" in ";".join(compiler.filters)


def test_overlay_uses_half_up_frame_count() -> None:
    compiler = Compiler(Plan(source="known.mp4"), durations={"known.mp4": 0.75}, frame_rate=30)
    compiler.retime(Retime(speed=1))
    compiler.overlay(Overlay(source="layer.mp4"))
    assert "trim=end_frame=23,setpts=N/(30*TB)" in ";".join(compiler.filters)


@pytest.mark.parametrize("consumer", ["overlay", "zoom"])
def test_mixed_rate_picture_preserves_tail(consumer: str, tmp_path: Path, record_property: Property) -> None:
    source = str(ensure_clips()["alpha"].path.resolve())
    incoming = tmp_path / "sixty.mp4"
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "nullsrc=s=640x360:r=60:d=3,geq=lum='16+40*T':cb=170:cr=128",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(incoming),
        ]
    )
    plan = Plan(source=source).with_operation(AudioRemove())
    if consumer == "overlay":
        plan = plan.with_operation(Retime(speed=1.5))
    plan = plan.with_operation(Stitch(sources=[str(incoming)]))
    operation = Overlay(source=source, width=32, height=32) if consumer == "overlay" else Zoom(to=1)
    plan = plan.with_operation(operation)
    output = tmp_path / "mixed.mp4"
    measured = measure(plan, output, record_property)
    expected_end = 5 if consumer == "overlay" else 6
    assert measured.pts[-1] + float(1 / measured.fps) == pytest.approx(expected_end, abs=1 / 30), (
        "[picture-gap] mixed tail endpoint"
    )
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(output),
            "-vf",
            "crop=64:64:200:100",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        timeout=30,
    )
    assert decoded.returncode == 0, "[render-refused] tail decode"
    last_luma = sum(decoded.stdout[-4096:]) / 4096
    record_property("last_luma", last_luma)
    assert last_luma > 120, f"[picture-content] discarded incoming tail: luma {last_luma}"


@pytest.mark.parametrize("audio_seconds", [3, 6])
@pytest.mark.parametrize("consumer", ["mix", "overlay"])
def test_stitch_mix_keeps_late_impulse(
    consumer: str, audio_seconds: int, tmp_path: Path, record_property: Property
) -> None:
    """The stitched clip's late base impulse survives the amix that follows.

    `audio_seconds=6` is the shape that actually loses it: a clip whose sound
    runs past its picture, cut back to the picture by `atrim`. On ffmpeg 6.1.1
    amix input 0 going EOF drops whatever is still in its FIFO
    (af_amix.c, `if (i == 0) s->input_state[i] = 0;`, fixed upstream), so with
    real audio on input 0 the last ~0.5 s became silence. The 3 s clip never
    reaches that path on either build, which is why it alone could not tell
    a silent clock on input 0 from no clock at all.
    """
    from tests.test_timing_verification_v2 import decode

    source = str(ensure_clips()["alpha"].path.resolve())
    incoming, bed = tmp_path / "tail.mov", tmp_path / "bed.wav"
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=640x360:r=30:d=3",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc='if(eq(n,134400),0.9,0)':s=48000:d={audio_seconds}",
            "-c:v",
            "libx264",
            "-c:a",
            "pcm_s16le",
            str(incoming),
        ]
    )
    assert decode(incoming)[:2] == (48000 * audio_seconds, [134400]), "[fixture] late impulse source"
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "6", str(bed)])
    plan = Plan(source=source).with_operation(Stitch(sources=[str(incoming)]))
    operation = (
        AudioMix(track=str(bed))
        if consumer == "mix"
        else Overlay(source=str(incoming), audio=LayerAudio(policy="keep"))
    )
    measured = measure(plan.with_operation(operation), tmp_path / "mixed.mp4", record_property)
    measured.audio()
    measured.picture(180)
    assert 278400 in measured.markers, f"[audio-marker] late base impulse was padded away: {measured.markers}"


@pytest.mark.parametrize("uri", [False, True])
def test_silent_regular_uri_is_probed(uri: bool, tmp_path: Path, record_property: Property) -> None:
    clip = ensure_silent_clip().path.resolve()
    source = clip.as_uri() if uri else str(clip)
    plan = Plan(source=source).with_operation(Retime(ramp=[RampPoint(at=0, speed=2), RampPoint(at=2, speed=2)]))
    printed = subprocess.run(
        [sys.executable, "-m", "vid.cli", "render", str(tmp_path / "probe.mp4"), "--print-command"],
        input=plan.model_dump_json(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert printed.returncode == 0, printed.stderr
    assert "not on this machine" not in printed.stderr, (
        f"[uri-local] a present local file was treated as missing: {printed.stderr}"
    )
    measured = measure(plan, tmp_path / "silent.mp4", record_property)
    measured.picture(30)
    assert "-an" in measured.argv, "[graph-structure] silent local URI invented an audio stream"


def test_long_ramp_expression_renders(tmp_path: Path, record_property: Property) -> None:
    source = str(ensure_clips()["alpha"].path.resolve())
    points = [RampPoint(at=i * 0.025, speed=0.8 if i % 2 else 1.6) for i in range(121)]
    measured = measure(
        Plan(source=source).with_operation(Retime(ramp=points)), tmp_path / "long-ramp.mp4", record_property
    )
    measured.picture(75)
    measured.audio()


def test_extra_track_unknown_dependency_is_disclosed(tmp_path: Path) -> None:
    from vid import lib

    source = str(ensure_clips()["alpha"].path.resolve())
    track = str(tmp_path / "absent.wav")
    assumptions: list[str] = []
    argv = compile_plan(
        Plan(source=source).with_operation(AudioReplace(track=track)),
        str(tmp_path / "x.mp4"),
        durations={source: 3},
        has_audio=True,
        assumptions=assumptions,
    )
    assert f"audio track input 1 ({track!r}) has an audio stream" in assumptions, (
        "[note-clause] extra input not registered"
    )
    assert "[1:a]" in argv[argv.index("-filter_complex") + 1]
    printed = lib.render(
        Plan(source=source).with_operation(AudioReplace(track=str(ensure_clips()["bravo"].path.resolve()))),
        str(tmp_path / "known.mp4"),
        print_command=True,
    )
    assert isinstance(printed, str)
