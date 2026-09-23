"""Production regressions measured at the decoded-audio and picture-packet boundaries."""

import json
import math
import struct
import subprocess

from pydantic import ValidationError
import pytest
from typer.testing import CliRunner

from tests.fixtures import ensure_clips, have_ffmpeg
from vid import lib
from vid.cli import app
from vid.compile import compile_plan
from vid.plan import AudioMix, AudioReplace, Caption, Cut, Grade, Plan, Retime, Stitch, Trim, Zoom
from vid.schemas import VidError


@pytest.mark.parametrize("op", [AudioReplace, AudioMix])
@pytest.mark.parametrize("start", [-1, float("nan"), float("inf"), -float("inf")])
def test_offsets_rejected_in_plan_and_library(op, start):
    with pytest.raises(ValidationError):
        op(track="tone.wav", start=start)
    method = lib.audio_replace if op is AudioReplace else lib.audio_mix
    with pytest.raises(VidError, match="nonnegative"):
        method(Plan(source="source.mp4"), "tone.wav", start=start)


@pytest.mark.parametrize("verb", ["mix", "replace"])
def test_offset_cli_and_legacy_plan(verb):
    runner = CliRunner()
    result = runner.invoke(app, ["audio", verb, "a.mp4", "--with", "b.wav", "--start", "1.25"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["operations"][0]["start"] == 1.25
    legacy = Plan.model_validate({"source": "a.mp4", "operations": [{"op": f"audio_{verb}", "track": "b.wav"}]})
    assert isinstance(legacy.operations[0], (AudioMix, AudioReplace))
    assert legacy.operations[0].start == 0
    bad = runner.invoke(app, ["audio", verb, "a.mp4", "--with", "b.wav", "--start", "-1"])
    assert bad.exit_code != 0


@pytest.mark.parametrize("op", [AudioReplace(track="b.wav"), AudioMix(track="b.wav")])
def test_compiler_refuses_unbounded_padding(op):
    with pytest.raises(VidError, match="known positive"):
        compile_plan(Plan(source="a.mp4").with_operation(op), "out.mp4")


@pytest.fixture
def media(tmp_path):
    if not have_ffmpeg():
        pytest.skip("requires ffmpeg")
    clips = ensure_clips()
    track = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=660:duration=0.4", "-ac", "2", str(track)],
        check=True,
        capture_output=True,
    )
    return str(clips["alpha"].path), str(track)


def decoded(path: str) -> tuple[float, ...]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    return struct.unpack(f"<{len(raw) // 4}f", raw)


def amplitude(samples: tuple[float, ...], hz: int, start: float, length: float = 0.1) -> float:
    window = samples[round(start * 8000) : round((start + length) * 8000)]
    assert window, "measurement window fell outside decoded audio"
    real = sum(v * math.cos(2 * math.pi * hz * i / 8000) for i, v in enumerate(window))
    imag = sum(v * math.sin(2 * math.pi * hz * i / 8000) for i, v in enumerate(window))
    return 2 * math.hypot(real, imag) / len(window)


@pytest.mark.parametrize("mix", [False, True])
@pytest.mark.parametrize("start", [0.0, 0.75, 2.8, 3.5, 1e308])
def test_decoded_supplied_audio_starts_at_offset_and_stops_at_picture(media, tmp_path, mix, start):
    source, track = media
    plan = (
        lib.audio_mix(Plan(source=source), track, level=0, start=start)
        if mix
        else lib.audio_replace(Plan(source=source), track, start=start)
    )
    out = lib.render(plan, str(tmp_path / "out.mp4"))
    samples = decoded(out)
    assert len(samples) / 8000 == pytest.approx(3.0, abs=0.035)
    if start < 3:
        assert amplitude(samples, 660, start + 0.05) > 0.04
    if start > 0:
        assert amplitude(samples, 660, min(start - 0.15, 1.0)) < 0.003
    if start + 0.55 < 2.8:
        assert amplitude(samples, 660, start + 0.5) < 0.003
    assert (amplitude(samples, 440, 1.5) > 0.08) == mix


def test_offsets_follow_operation_order(media, tmp_path):
    source, track = media
    original = Plan(source=source)
    before_trim = lib.trim(lib.audio_replace(original, track, start=1.25), "1", "3")
    after_trim = lib.audio_replace(lib.trim(original, "1", "3"), track, start=1.25)
    for name, plan, onset in [("before", before_trim, 0.25), ("after", after_trim, 1.25)]:
        samples = decoded(lib.render(plan, str(tmp_path / f"{name}.mp4")))
        assert amplitude(samples, 660, onset - 0.15) < 0.003
        assert amplitude(samples, 660, onset + 0.05) > 0.04


def picture_packets(path: str) -> list[dict]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_packets",
            "-show_data_hash",
            "sha256",
            "-show_entries",
            "packet=pts_time,dts_time,duration_time,size,data_hash",
            "-of",
            "json",
            path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    packets = json.loads(result.stdout)["packets"]
    assert packets
    return packets


@pytest.mark.parametrize("kind", ["remove", "replace", "mix"])
@pytest.mark.parametrize("extension", [".mp4", ".mov"])
def test_copy_preserves_every_picture_packet_and_timing(media, tmp_path, kind, extension):
    source, track = media
    if extension == ".mov":
        remux = str(tmp_path / "source.mov")
        subprocess.run(["ffmpeg", "-v", "error", "-i", source, "-c", "copy", remux], check=True, capture_output=True)
        source = remux
    plan = Plan(source=source)
    if kind == "remove":
        plan = lib.audio_remove(plan)
    elif kind == "replace":
        plan = lib.audio_replace(plan, track, start=0.75)
    else:
        plan = lib.audio_mix(plan, track, level=0, start=0.75)
    out = lib.render(plan, str(tmp_path / f"out{extension}"), video_codec="copy")
    assert picture_packets(out) == picture_packets(source)
    if kind != "remove":
        samples = decoded(out)
        assert amplitude(samples, 660, 0.55) < 0.003
        assert amplitude(samples, 660, 0.8) > 0.04


@pytest.mark.parametrize(
    "op",
    [
        Trim(),
        Cut(start=0, end=1),
        Retime(speed=1),
        Zoom(),
        Grade(look="warm"),
        Caption(subtitles="a.srt"),
        Stitch(sources=["b.mp4"]),
    ],
)
def test_copy_rejects_picture_operations_before_any_probe(op, tmp_path):
    with pytest.raises(VidError, match="audio-only"):
        lib.render(Plan(source="missing.mp4").with_operation(op), str(tmp_path / "out.mp4"), video_codec="copy")


@pytest.mark.parametrize("extension", [".mkv", ".ts", ".mov"])
def test_copy_refuses_unqualified_container_changes(extension):
    with pytest.raises(VidError, match="container"):
        compile_plan(Plan(source="a.mp4"), f"b{extension}", video_codec="copy", durations={"a.mp4": 3})


def test_copy_bounds_audio_to_picture_not_longer_container(media, tmp_path):
    source, track = media
    longer = str(tmp_path / "long-audio.mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            source,
            "-f",
            "lavfi",
            "-i",
            "sine=duration=5",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            longer,
        ],
        check=True,
        capture_output=True,
    )
    out = lib.render(lib.audio_replace(Plan(source=longer), track), str(tmp_path / "out.mp4"), video_codec="copy")
    assert len(decoded(out)) / 8000 == pytest.approx(3, abs=0.035)
    assert picture_packets(out) == picture_packets(longer)


def test_incoming_and_outgoing_handles_survive_two_dissolves(media, tmp_path):
    source, _ = media
    track = str(tmp_path / "speech.wav")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=660:duration=1.4", track],
        check=True,
        capture_output=True,
    )
    voiced = lib.render(
        lib.audio_replace(Plan(source=source), track, start=0.8), str(tmp_path / "voiced.mp4"), video_codec="copy"
    )
    plan = lib.stitch(None, [voiced, voiced, voiced], transition="fade", duration=0.8)
    samples = decoded(lib.render(plan, str(tmp_path / "joined.mp4")))
    assert len(samples) / 8000 == pytest.approx(7.4, abs=0.04)
    for onset, end in [(0.8, 2.2), (3.0, 4.4), (5.2, 6.6)]:
        assert amplitude(samples, 660, onset - 0.13, 0.05) < 0.003
        assert amplitude(samples, 660, onset + 0.05) > 0.09
        assert amplitude(samples, 660, end - 0.15) > 0.09
        assert amplitude(samples, 660, end + 0.07, 0.05) < 0.003


def test_copy_rejects_shifted_picture_timestamps(media, tmp_path):
    source, _ = media
    shifted = str(tmp_path / "shifted.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-itsoffset", "1", "-i", source, "-c", "copy", shifted],
        check=True,
        capture_output=True,
    )
    with pytest.raises(VidError, match="starting at zero"):
        lib.render(lib.audio_remove(Plan(source=shifted)), str(tmp_path / "out.mp4"), video_codec="copy")


def test_cli_copy_preserves_prores_picture_format_and_packets(media, tmp_path):
    _, track = media
    source = str(tmp_path / "source.mov")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=96x64:d=1",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4",
            "-pix_fmt",
            "yuva444p10le",
            source,
        ],
        check=True,
        capture_output=True,
    )
    plan = lib.audio_replace(Plan(source=source), track, start=0.25)
    output = str(tmp_path / "out.mov")
    result = CliRunner().invoke(app, ["render", output, "--video-codec", "copy"], input=plan.model_dump_json())
    assert result.exit_code == 0, result.output
    assert picture_packets(output) == picture_packets(source)
    samples = decoded(output)
    assert amplitude(samples, 660, 0.05) < 0.003
    assert amplitude(samples, 660, 0.3) > 0.04


def test_copy_rejects_multiple_picture_streams(media, tmp_path):
    source, _ = media
    multiple = str(tmp_path / "multiple.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", source, "-map", "0:v:0", "-map", "0:v:0", "-c", "copy", multiple],
        check=True,
        capture_output=True,
    )
    with pytest.raises(VidError, match="exactly one"):
        lib.render(lib.audio_remove(Plan(source=multiple)), str(tmp_path / "out.mp4"), video_codec="copy")


@pytest.mark.parametrize("codec", ["libx264", "copy"])
def test_delayed_existing_audio_keeps_picture_alignment(media, tmp_path, codec):
    source, _ = media
    delayed = str(tmp_path / "delayed.mp4")
    silent = str(tmp_path / "silent.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            source,
            "-itsoffset",
            "1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=1",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            delayed,
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc", "-t", "3", silent],
        check=True,
        capture_output=True,
    )
    mixed = lib.render(lib.audio_mix(Plan(source=delayed), silent), str(tmp_path / "mixed.mp4"), video_codec=codec)
    samples = decoded(mixed)
    assert amplitude(samples, 660, 0.3) < 0.003
    assert amplitude(samples, 660, 1.3) > 0.09
    if codec == "copy":
        assert picture_packets(mixed) == picture_packets(delayed)
    joined = lib.render(
        lib.stitch(None, [delayed, delayed], transition="fade", duration=0.5),
        str(tmp_path / "joined-delay.mp4"),
    )
    samples = decoded(joined)
    for origin in [0, 2.5]:
        assert amplitude(samples, 660, origin + 0.3) < 0.003
        assert amplitude(samples, 660, origin + 1.3) > 0.09


@pytest.mark.parametrize("codec", ["libx264", "copy"])
def test_beyond_picture_offset_is_inaudible_with_long_source_audio(media, tmp_path, codec):
    source, track = media
    long_source = str(tmp_path / "long-source.mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            source,
            "-f",
            "lavfi",
            "-i",
            "sine=duration=5",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            long_source,
        ],
        check=True,
        capture_output=True,
    )
    out = lib.render(
        lib.audio_replace(Plan(source=long_source), track, start=4),
        str(tmp_path / "bounded-offset.mp4"),
        video_codec=codec,
    )
    samples = decoded(out)
    assert len(samples) / 8000 == pytest.approx(3, abs=0.035)
    assert max(abs(value) for value in samples) < 0.003


def test_picture_duration_fallback_without_stream_duration(media, tmp_path):
    from vid.probe import video_duration

    source, track = media
    path = str(tmp_path / "long-audio.mkv")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            source,
            "-f",
            "lavfi",
            "-i",
            "sine=duration=5",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "pcm_s16le",
            path,
        ],
        check=True,
        capture_output=True,
    )
    assert video_duration(path) == pytest.approx(3, abs=0.01)
    out = lib.render(lib.audio_replace(Plan(source=path), track, start=4), str(tmp_path / "out.mp4"))
    samples = decoded(out)
    assert len(samples) / 8000 == pytest.approx(3, abs=0.035)
    assert max(abs(value) for value in samples) < 0.003
