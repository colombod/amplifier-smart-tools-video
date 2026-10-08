"""Public printed commands, independently decoded PCM, and tagged failure boundaries."""

from __future__ import annotations

from array import array
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
import itertools
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

from tests.fixtures import ensure_clips, have_ffmpeg
from vid.compile import Compiler
from vid.plan import AudioRemove, Overlay, Plan, RampPoint, Retime, Stitch, Trim, Zoom
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="real encoded fixtures and decoded outputs")
Property = Callable[[str, object], None]


def command(argv: list[str], stdin: str | None = None, tag: str = "render-refused") -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"[{tag}] {argv!r}\n{result.stderr}"
    return result


def labels(entry: str) -> tuple[list[str], list[str]]:
    leading = re.match(r"^(?:\[[^]]+\])*", entry)
    trailing = re.search(r"(?:\[[^]]+\])*$", entry)
    assert leading is not None, "[graph-structure] leading label parser"
    assert trailing is not None, "[graph-structure] trailing label parser"
    return re.findall(r"\[([^]]+)\]", leading[0]), re.findall(r"\[([^]]+)\]", trailing[0])


def values(argv: list[str], flag: str) -> list[str]:
    return [value for index, value in enumerate(argv) if index and argv[index - 1] == flag]


def decode(output: Path) -> tuple[int, list[int], int, int]:
    data = json.loads(
        command(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_streams", "-of", "json", str(output)]
        ).stdout
    )
    stream = data["streams"][0]
    rate, channels = int(stream["sample_rate"]), int(stream["channels"])
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(output), "-map", "0:a:0", "-f", "s32le", "-c:a", "pcm_s32le", "-"],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, f"[render-refused] independent decode: {result.stderr!r}"
    assert len(result.stdout) % (4 * channels) == 0, "[audio-samples] incomplete decoded sample"
    samples = array("i")
    samples.frombytes(result.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    markers = [index // channels for index in range(0, len(samples), channels) if abs(samples[index]) >= 2**30]
    return len(samples) // channels, markers, rate, channels


@dataclass(frozen=True)
class Measurement:
    frames: int
    fps: Fraction
    pts: list[float]
    pcm_samples: int | None
    aac_samples: int | None
    expected: Fraction | None
    markers: list[int]
    argv: list[str]

    def picture(self, expected_frames: int | None = None) -> None:
        if expected_frames is not None:
            assert self.frames == expected_frames, f"[picture-count] {self.frames} != {expected_frames}"
        assert self.pts, "[picture-gap] no picture frames"
        assert self.pts[0] == 0, f"[picture-gap] first timestamps {self.pts[:2]}"
        gaps = [b - a for a, b in itertools.pairwise(self.pts)]
        assert not gaps or max(gaps) <= float(1 / self.fps) + 1e-6, f"[picture-gap] {max(gaps)}"
        assert self.pts[-1] + float(1 / self.fps) == pytest.approx(float(self.frames / self.fps), abs=1e-6), (
            "[picture-gap] picture endpoint"
        )

    def audio(self) -> None:
        assert self.pcm_samples is not None, "[audio-samples] missing diagnostic audio"
        assert self.expected is not None, "[audio-samples] missing expected clock"
        assert abs(self.pcm_samples - self.expected) <= 1, (
            f"[audio-samples] decoded PCM {self.pcm_samples}, picture-clock E {self.expected}, gap {self.pcm_samples - self.expected} samples"
        )
        assert self.aac_samples is not None, "[audio-samples] missing AAC audio"
        assert abs(self.aac_samples - self.pcm_samples) <= 1024, (
            f"[audio-samples] codec framing, not exact edit timing: AAC {self.aac_samples}, PCM {self.pcm_samples}"
        )


def measure(plan: Plan, output: Path, record_property: Property) -> Measurement:
    printed = command(
        [sys.executable, "-m", "vid.cli", "render", str(output), "--print-command"], plan.model_dump_json()
    )
    assert not output.exists(), "[render-refused] print-command unexpectedly rendered"
    assert "note:" not in printed.stderr, f"[note-clause] {printed.stderr}"
    argv = shlex.split(printed.stdout)
    record_property("aac_argv", json.dumps(argv))
    command(argv)
    picture = json.loads(
        command(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_streams",
                "-show_frames",
                "-of",
                "json",
                str(output),
            ]
        ).stdout
    )
    pts = [float(frame["best_effort_timestamp_time"]) for frame in picture["frames"]]
    fps = Fraction(picture["streams"][0]["r_frame_rate"])
    frames = len(pts)
    record_property("F", frames)
    record_property("fps", str(fps))
    if "-c:a" not in argv:
        return Measurement(frames, fps, pts, None, None, None, [], argv)
    assert values(argv, "-c:a") == ["aac"], "[graph-structure] diagnostic codec prerequisite"
    assert "-b:a" not in argv, "[graph-structure] unexpected audio bitrate"
    pcm = output.with_suffix(".pcm.mov")
    diagnostic = argv.copy()
    diagnostic[diagnostic.index("-c:a") + 1] = "pcm_s24le"
    diagnostic[-1] = str(pcm)
    assert values(argv, "-filter_complex") == values(diagnostic, "-filter_complex"), (
        "[graph-structure] diagnostic graph changed"
    )
    assert values(argv, "-map") == values(diagnostic, "-map"), "[graph-structure] diagnostic maps changed"
    assert [index for index, (a, b) in enumerate(zip(argv, diagnostic, strict=True)) if a != b] == [
        argv.index("-c:a") + 1,
        len(argv) - 1,
    ], "[graph-structure] diagnostic changed more than codec and output"
    record_property("pcm_argv", json.dumps(diagnostic))
    command(diagnostic)
    count, markers, rate, _ = decode(pcm)
    aac_count, _, _, _ = decode(output)
    stream = json.loads(
        command(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_streams", "-of", "json", str(pcm)]).stdout
    )["streams"][0]
    packet = json.loads(
        command(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-read_intervals",
                "%+#1",
                "-show_packets",
                "-of",
                "json",
                str(pcm),
            ]
        ).stdout
    )["packets"][0]
    expected = frames / fps * rate
    for name, value in {
        "N_A": aac_count,
        "N_B": count,
        "E": str(expected),
        "sr": rate,
        "markers": json.dumps(markers),
        "duration_ts": stream["duration_ts"],
        "time_base": stream["time_base"],
        "first_packet_pts": packet["pts"],
        "sample_delta": str(count - expected),
    }.items():
        record_property(name, value)
    assert Fraction(stream["time_base"]) == Fraction(1, rate), f"[audio-samples] PCM time base {stream['time_base']}"
    assert int(stream["duration_ts"]) == count, (
        f"[audio-samples] PCM duration_ts {stream['duration_ts']} != decoded {count}"
    )
    assert int(packet["pts"]) == 0, f"[audio-samples] PCM first packet PTS {packet['pts']}"
    return Measurement(frames, fps, pts, count, aac_count, expected, markers, argv)


FIXTURES = {
    "R": (3, 3, [48000]),
    "RL": (3, 6, [48000, 216000]),
    "I": (3, 3, [24000]),
    "IL": (3, 6, [24000, 192000]),
    "IT": (3, 3, [48000]),
    "ILT": (3, 6, [48000, 192000]),
    "R6": (6, 6, [48000]),
}


@pytest.fixture(scope="module")
def marker_clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    directory = tmp_path_factory.mktemp("pcm-markers")
    clips: dict[str, str] = {}
    for name, (video_seconds, audio_seconds, indices) in FIXTURES.items():
        output = directory / f"{name}.mov"
        expression = "+".join(f"eq(n,{index})" for index in indices)
        command(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size=320x240:rate=30:duration={video_seconds}",
                "-f",
                "lavfi",
                "-i",
                f"aevalsrc='if({expression},0.9,0)':s=48000:d={audio_seconds}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            tag="fixture",
        )
        count, measured, rate, channels = decode(output)
        assert (count, measured, rate, channels) == (audio_seconds * 48000, indices, 48000, 1), (
            f"[fixture] {name}: {(count, measured, rate, channels)}"
        )
        clips[name] = str(output)
    return clips


STITCH_CASES = [
    ("R-I", "R", "I", False, 180, 288000, [48000, 168000]),
    ("RL-I", "RL", "I", False, 180, 288000, [48000, 168000]),
    ("R-IL", "R", "IL", False, 180, 288000, [48000, 168000]),
    ("R6-I", "R6", "I", False, 270, 432000, [48000, 312000]),
    ("R-IT", "R", "IT", True, 165, 264000, [48000, 168000]),
    ("RL-IT", "RL", "IT", True, 165, 264000, [48000, 168000]),
    ("R-ILT", "R", "ILT", True, 165, 264000, [48000, 168000]),
]


@pytest.mark.parametrize(
    ("running", "incoming", "fade", "frames", "samples", "markers"),
    [case[1:] for case in STITCH_CASES],
    ids=[case[0] for case in STITCH_CASES],
)
def test_stitch_markers(
    marker_clips: dict[str, str],
    running: str,
    incoming: str,
    fade: bool,
    frames: int,
    samples: int,
    markers: list[int],
    tmp_path: Path,
    record_property: Property,
) -> None:
    operation = Stitch(sources=[marker_clips[incoming]], transition="fade" if fade else None, transition_duration=0.5)
    measured = measure(
        Plan(source=marker_clips[running]).with_operation(operation), tmp_path / "edit.mp4", record_property
    )
    assert measured.markers == markers, f"[audio-marker] measured {measured.markers}, expected {markers}"
    assert measured.pcm_samples == samples, f"[audio-samples] {measured.pcm_samples} != {samples}"
    measured.audio()
    measured.picture(frames)


def alpha_plan() -> Plan:
    return Plan(source=str(ensure_clips()["alpha"].path.resolve()))


def test_constant_tie_renders(tmp_path: Path, record_property: Property) -> None:
    measured = measure(alpha_plan().with_operation(Retime(speed=4)), tmp_path / "constant.mp4", record_property)
    measured.picture(23)
    measured.audio()
    assert measured.expected == 36800, f"[audio-samples] E {measured.expected}"


def test_overlay_half_frame_renders(tmp_path: Path, record_property: Property) -> None:
    bravo = str(ensure_clips()["bravo"].path.resolve())
    plan = (
        alpha_plan()
        .with_operation(Retime(speed=2))
        .with_operation(Trim(start=0, end=0.75))
        .with_operation(Overlay(source=bravo))
        .with_operation(AudioRemove())
    )
    # ffmpeg 6.1.1 lost the final frame (22, ending 0.733333) when the regrid sat
    # AFTER `overlay`; the same plan without the overlay keeps 23 on both builds.
    measured = measure(plan, tmp_path / "overlay.mp4", record_property)
    measured.picture(23)
    assert round(measured.pts[-1] * 30) == 22, f"[picture-gap] final frame at {measured.pts[-1]}"


def test_zoom_then_overlay_half_frame_renders(tmp_path: Path, record_property: Property) -> None:
    bravo = str(ensure_clips()["bravo"].path.resolve())
    plan = (
        alpha_plan()
        .with_operation(Trim(start=0, end=0.75))
        .with_operation(Zoom(to=1))
        .with_operation(Overlay(source=bravo))
        .with_operation(AudioRemove())
    )
    # The edit is 22.5 frames at 30 Hz; zoom establishes the grid without
    # rounding the edit length, so overlay itself must keep the 23rd frame.
    measured = measure(plan, tmp_path / "zoom-overlay.mp4", record_property)
    measured.picture(23)


@pytest.mark.parametrize(("lo", "hi", "frames"), [(0.5, 2, 83), (2, 2, 45), (4, 4, 23), (0.25, 3.25, 74)])
def test_ramp_audio_finishes(lo: float, hi: float, frames: int, tmp_path: Path, record_property: Property) -> None:
    # These are the supplied fixture counts, not a docs-derived segmentation oracle.
    # docs/ARCHITECTURE.md:63 leaves the region count/interpolation unspecified.
    plan = alpha_plan().with_operation(Retime(ramp=[RampPoint(at=0, speed=lo), RampPoint(at=3, speed=hi)]))
    measured = measure(plan, tmp_path / "ramp.mp4", record_property)
    measured.audio()
    measured.picture(frames)


def test_zero_frame_ramp_refuses_with_diagnosis(tmp_path: Path) -> None:
    output = tmp_path / "empty.mp4"
    plan = alpha_plan().with_operation(Retime(ramp=[RampPoint(at=0, speed=100), RampPoint(at=0.01, speed=100)]))
    result = subprocess.run(
        [sys.executable, "-m", "vid.cli", "render", str(output)],
        input=plan.model_dump_json(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, "[refusal-message] zero-frame render succeeded"
    assert "keeps less than one output frame" in result.stderr, f"[refusal-message] {result.stderr}"
    assert "unknown or empty" not in result.stderr, f"[refusal-message] wrong backstop: {result.stderr}"
    assert not output.exists(), "[refusal-message] zero-frame output exists"


@pytest.mark.parametrize("case", ["copy", "sink", "unused", "known"])
def test_audio_dependency_note(case: str, tmp_path: Path) -> None:
    missing = str(tmp_path / "missing.mov")
    plan = Plan(source=missing) if case != "known" else alpha_plan()
    if case == "sink":
        plan = plan.with_operation(Retime(speed=2)).with_operation(AudioRemove())
    elif case == "unused":
        plan = plan.with_operation(AudioRemove()).with_operation(Trim(start=0, end=1))
    elif case == "copy":
        pass
    else:
        plan = plan.with_operation(Retime(speed=2))
    if case == "copy":
        from vid.compile import compile_plan

        assumptions: list[str] = []
        argv = compile_plan(
            plan,
            str(tmp_path / "note.mov"),
            video_codec="copy",
            durations={missing: 3},
            has_audio=None,
            pure_compile=True,
            assumptions=assumptions,
        )
        stderr = "; ".join(assumptions)
    else:
        result = command(
            [sys.executable, "-m", "vid.cli", "render", str(tmp_path / "note.mp4"), "--print-command"],
            plan.model_dump_json(),
        )
        argv = shlex.split(result.stdout)
        stderr = result.stderr
    inputs = values(argv, "-i")
    used = set(values(argv, "-map"))
    for graph in values(argv, "-filter_complex"):
        used.update(label for entry in graph.split(";") for label in labels(entry)[0])
    expected = {
        f"{'source' if index == 0 else 'stitch'} input {index} ({path!r}) has an audio stream"
        for index, path in enumerate(inputs)
        if not Path(path).exists() and f"{index}:a" in used
    }
    actual = set(re.findall(r"(?:source|stitch|overlay) input \d+ \([^\n]+?\) has (?:an|no) audio stream", stderr))
    assert actual == expected, f"[note-clause] actual {actual}, expected {expected}, stderr {stderr}"


RAMP_GRID = [(0.5, 2), (2, 2), (4, 4), (0.25, 3.25), (3, 0.5), (0.75, 4)]
TRIM_GRID = [None, (0.5, 2.5), (0.25, 2.9)]


@pytest.mark.parametrize("overlay", [False, True], ids=["ramp", "overlay"])
@pytest.mark.parametrize("removed", [False, True], ids=["retained", "removed"])
@pytest.mark.parametrize("trim", TRIM_GRID, ids=["none", "half", "quarter"])
@pytest.mark.parametrize(("lo", "hi"), RAMP_GRID)
def test_final_trim_classification(
    lo: float,
    hi: float,
    trim: tuple[float, float] | None,
    removed: bool,
    overlay: bool,
    tmp_path: Path,
    record_property: Property,
) -> None:
    plan = alpha_plan()
    duration = 3.0
    if trim is not None:
        plan = plan.with_operation(Trim(start=trim[0], end=trim[1]))
        duration = trim[1] - trim[0]
    if removed:
        plan = plan.with_operation(AudioRemove())
    plan = plan.with_operation(Retime(ramp=[RampPoint(at=0, speed=lo), RampPoint(at=duration, speed=hi)]))
    if overlay:
        plan = plan.with_operation(Overlay(source=str(ensure_clips()["bravo"].path.resolve())))
    measured = measure(plan, tmp_path / "grid.mp4", record_property)
    measured.picture()
    if not removed:
        measured.audio()
    hashes = command(
        ["ffmpeg", "-v", "error", "-i", str(tmp_path / "grid.mp4"), "-map", "0:v", "-f", "framemd5", "-"]
    ).stdout
    record_property("framemd5", json.dumps([line for line in hashes.splitlines() if not line.startswith("#")]))


def test_trim_half_frame_discovery(tmp_path: Path, record_property: Property) -> None:
    measured = measure(alpha_plan().with_operation(Trim(start=0, end=0.75)), tmp_path / "trim.mp4", record_property)
    # Discovery only: the broader Trim endpoint issue is tracked separately.
    record_property(
        "discovery_trim_delta",
        str(measured.pcm_samples - measured.expected)
        if measured.pcm_samples is not None and measured.expected is not None
        else "missing",
    )


def test_aac_input_excess_discovery(record_property: Property) -> None:
    for name, clip in ensure_clips().items():
        count, _, rate, _ = decode(clip.path)
        stream = json.loads(
            command(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", str(clip.path)]
            ).stdout
        )["streams"][0]
        record_property(f"input_{name}_aac_excess", count - round(float(stream["duration"]) * rate))


def test_pure_audio_bound_note() -> None:
    compiler = Compiler(Plan(source="missing.mov"), pure_compile=True)
    try:
        compiler._audio_on_picture("0:a", None, "stitch", "running audio length bound", "edit length")
    except VidError as error:
        pytest.fail(f"[note-clause] fallback refused instead of describing omitted bound: {error}")
    assert compiler.omissions == ["stitch: apad,atrim,asetpts (running audio length bound, needs edit length)"], (
        "[note-clause] omitted independent running bound"
    )


def test_structural_guards() -> None:
    for length in [0, -1, float("nan"), float("inf")]:
        compiler = Compiler(Plan(source="known.mov"), durations={"known.mov": 3})
        refused = False
        try:
            compiler._audio_on_picture("0:a", length, "stitch", "running bound", "edit length")
        except VidError:
            refused = True
        assert refused, f"[graph-structure] invalid bound accepted: {length}"
    compiler = Compiler(Plan(source="unknown.mov"), pure_compile=True, has_audio=False)
    compiler.retime(Retime(ramp=[RampPoint(at=1, speed=2), RampPoint(at=2, speed=2)]))
    assert compiler.elapsed is None, "[graph-structure] nominal ramp cap promoted to measured duration"
