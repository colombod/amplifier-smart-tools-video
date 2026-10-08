"""`--print-command` must print the command `render` actually runs.

`vid render --help` promises "Print the ffmpeg it would run." That is a
FIDELITY claim, and it is the only thing this flag is for. Two earlier attempts
broke it from opposite sides:

  - probing unconditionally made a pure compile demand ffprobe for the
    commonest plan there is, so `--print-command` failed on a machine with no
    ffmpeg -- the case it exists to serve;
  - skipping the probe entirely printed an `audio replace` graph with no
    `apad`/`atrim`, which RAN AS PRINTED put a 6 s track on a 3 s picture and
    produced a SIX SECOND file where `render` produces three.

The second is the more dangerous of the two, and it shipped, because the tests
guarding this flag asserted only that it did not REFUSE. A test that checks a
command was printed cannot tell a faithful command from a plausible wrong one.

SO THIS TEST RUNS WHAT IS PRINTED. For each plan it renders twice -- once
through `vid render`, once by executing the exact argv `--print-command`
emitted -- and compares the decoded results. Nothing short of that can catch a
divergence, which is precisely how the last one survived review.
"""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess

import pytest

from tests.fixtures import have_ffmpeg

REPO = Path(__file__).resolve().parents[1]
VID = str(REPO / ".venv" / "bin" / "vid")

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="renders real files")


def _ffprobe(path: Path) -> dict[str, str]:
    """Everything a divergence has been seen to hide in, plus decoded content.

    THE FIRST VERSION OF THIS COMPARED THREE FIELDS -- container duration,
    audio duration, audio channels -- and a review proved that too narrow. Two
    real regressions walked straight through it:

      - dropping the SIZE probe in print mode made `stitch --fit` print a
        command that exits 234, "Conversion failed", while `render` succeeds;
      - dropping the AUDIO-FORMAT probe made a 44.1 kHz mono source stitched
        with a silent clip come out at a different sample rate.

    Neither changes any of the three original fields. So this now compares the
    picture size and the audio sample rate as well, and hashes the DECODED
    streams: a divergence that alters no metadata at all still has to change
    the pixels or the samples, and that is the backstop.
    """

    def field(args: list[str]) -> str:
        return subprocess.run(
            ["ffprobe", "-v", "error", *args, "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
        ).stdout.strip()

    def decoded(args: list[str]) -> str:
        """md5 of the raw decoded stream -- content, not container metadata."""
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), *args, "-f", "md5", "-"],
            capture_output=True,
            text=True,
        )
        return out.stdout.strip() or f"<none:{out.returncode}>"

    return {
        "container duration": field(["-show_entries", "format=duration"]),
        "video size": field(["-select_streams", "v:0", "-show_entries", "stream=width,height"]),
        "audio duration": field(["-select_streams", "a:0", "-show_entries", "stream=duration"]),
        "audio rate/channels": field(["-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels"]),
        "decoded video": decoded(["-map", "0:v:0", "-c:v", "rawvideo"]),
        "decoded audio": decoded(["-map", "0:a:0?", "-c:a", "pcm_s16le"]),
    }


def _mismatch(rendered: dict[str, str], printed: dict[str, str], command: str) -> str:
    lines = [
        f"  {name}:\n      render       {rendered[name]}\n      printed, run {printed[name]}"
        for name in rendered
        if rendered[name] != printed[name]
    ]
    return (
        "--print-command printed a command that produces a DIFFERENT file.\n"
        + "\n".join(lines)
        + f"\n  command: {command}"
    )


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, str]:
    """A 3 s picture, and tracks deliberately LONGER and SHORTER than it.

    Equal lengths are what hid the defect: with a track the same length as the
    picture, a graph that pads/trims and one that does not produce the same
    file. The mismatch is the whole measurement.
    """
    d = tmp_path_factory.mktemp("fidelity")
    video = d / "video3s.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=15:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-ac",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    made = {"video": str(video)}
    for label, seconds in (("long", 6), ("short", 1)):
        track = d / f"track_{label}.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=880:duration={seconds}",
                "-ac",
                "1",
                str(track),
            ],
            check=True,
            capture_output=True,
        )
        made[label] = str(track)

    # A DIFFERENTLY SIZED clip, so `stitch --fit` has something to fit. Without
    # one, the size probe is a no-op and dropping it changes nothing -- which
    # is exactly why a review found that mutation surviving.
    wide = d / "wide640.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=15:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=2",
            "-ac",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(wide),
        ],
        check=True,
        capture_output=True,
    )
    made["wide"] = str(wide)

    # A 44.1 kHz MONO source, and a SILENT clip to stitch onto it. Every other
    # fixture here is 48 kHz, and at 48 kHz "keep the source format" and
    # "force 48 kHz stereo" emit the same thing -- so the audio-format probe
    # was untestable by construction, and dropping it survived too.
    mono441 = d / "mono441.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=15:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=2",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(mono441),
        ],
        check=True,
        capture_output=True,
    )
    made["mono441"] = str(mono441)

    silent = d / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=15:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(silent),
        ],
        check=True,
        capture_output=True,
    )
    made["silent"] = str(silent)

    # TWO SOURCES `--video-codec copy` CANNOT TOUCH, for the two different
    # reasons `copy_video_duration` refuses: a picture stream that does not
    # start at zero, and more than one video stream. Both probe fine under
    # `video_duration`, which reads only `v:0`'s duration -- so these are the
    # files where the two duration probes genuinely disagree.
    #
    # Measured on these exact constructions:
    #     video_duration=3.000000   copy_video_duration=REFUSES
    #
    # The disagreement is refusal-vs-value, not two different numbers. Both
    # functions read the same `stream=duration` field, so a numeric split would
    # need `video_duration`'s presentation-bounds fallback; what actually
    # differs is that stream-copy has preconditions the ordinary probe has not.
    offset = d / "copy_unsafe_offset.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video),
            "-c",
            "copy",
            "-avoid_negative_ts",
            "disabled",
            "-output_ts_offset",
            "0.5",
            str(offset),
        ],
        check=True,
        capture_output=True,
    )
    made["copy_unsafe_offset"] = str(offset)

    two = d / "copy_unsafe_two_streams.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video),
            "-i",
            str(video),
            "-map",
            "0:v",
            "-map",
            "1:v",
            "-map",
            "0:a",
            "-c",
            "copy",
            str(two),
        ],
        check=True,
        capture_output=True,
    )
    made["copy_unsafe_two_streams"] = str(two)
    return made


def _both_ways(
    plan: dict, tmp_path: Path, extra_args: tuple[str, ...] = ()
) -> tuple[dict[str, str], dict[str, str], str]:
    rendered, printed = tmp_path / "rendered.mp4", tmp_path / "printed.mp4"

    direct = subprocess.run(
        [VID, "render", str(rendered), *extra_args], input=json.dumps(plan), capture_output=True, text=True
    )
    assert direct.returncode == 0, f"render failed: {direct.stderr}"

    shown = subprocess.run(
        [VID, "render", str(printed), *extra_args, "--print-command"],
        input=json.dumps(plan),
        capture_output=True,
        text=True,
    )
    assert shown.returncode == 0, f"--print-command failed: {shown.stderr}"

    ran = subprocess.run(shlex.split(shown.stdout.strip()), capture_output=True, text=True)
    assert ran.returncode == 0, f"the PRINTED command would not run: {ran.stderr[-400:]}"

    return _ffprobe(rendered), _ffprobe(printed), shown.stdout.strip()


@pytest.mark.parametrize("track", ["long", "short"])
def test_audio_replace_prints_the_command_it_would_run(clips, tmp_path, track: str) -> None:
    """THE DEFECT AN EXTERNAL REVIEW CAUGHT.

    Measured before the fix, with the 6 s track: `render` produced a 3.0 s
    file, the printed command produced a 6.0 s one. With the 1 s track,
    `render` padded the audio to 3.0 s and the printed command left it at 1.0 s.
    """
    plan = {
        "plan_format": 1,
        "source": clips["video"],
        "operations": [{"op": "audio_replace", "track": clips[track]}],
    }
    rendered, printed, command = _both_ways(plan, tmp_path)
    assert rendered == printed, _mismatch(rendered, printed, command)


@pytest.mark.parametrize("track", ["long", "short"])
def test_copy_audio_replace_prints_decoded_fidelity(clips, tmp_path, track: str) -> None:
    plan = {
        "plan_format": 1,
        "source": clips["video"],
        "operations": [{"op": "audio_replace", "track": clips[track]}],
    }
    rendered, printed, command = _both_ways(plan, tmp_path, ("--video-codec", "copy"))
    argv = shlex.split(command)
    assert argv[argv.index("-c:v") + 1] == "copy", "[graph-structure] copy fidelity used another codec"
    for key in ("decoded video", "decoded audio"):
        assert rendered[key].startswith("MD5="), f"[decode-fidelity] {key}: {rendered[key]}"
        assert printed[key].startswith("MD5="), f"[decode-fidelity] {key}: {printed[key]}"
        assert rendered[key] == printed[key], f"[decode-fidelity] {_mismatch(rendered, printed, command)}"
    assert rendered == printed, _mismatch(rendered, printed, command)


@pytest.mark.parametrize("track", ["long", "short"])
def test_audio_mix_prints_the_command_it_would_run(clips, tmp_path, track: str) -> None:
    plan = {
        "plan_format": 1,
        "source": clips["video"],
        "operations": [{"op": "audio_mix", "track": clips[track], "level": -18}],
    }
    rendered, printed, command = _both_ways(plan, tmp_path)
    assert rendered == printed, _mismatch(rendered, printed, command)


@pytest.mark.parametrize(
    ("label", "operations"),
    [
        ("trim", [{"op": "trim", "start": "0", "end": "2"}]),
        ("retime", [{"op": "retime", "speed": 1.5}]),
        ("trim then replace", [{"op": "trim", "start": "0", "end": "2"}, {"op": "audio_replace", "track": "LONG"}]),
    ],
)
def test_other_plans_also_print_what_they_run(clips, tmp_path, label: str, operations: list) -> None:
    """The same fidelity claim, across plans whose lengths also matter."""
    operations = [{**op, "track": clips["long"]} if op.get("track") == "LONG" else op for op in operations]
    plan = {"plan_format": 1, "source": clips["video"], "operations": operations}
    rendered, printed, command = _both_ways(plan, tmp_path)
    assert rendered == printed, _mismatch(rendered, printed, command)


def test_when_the_media_is_absent_it_still_prints_and_says_what_is_missing() -> None:
    """The case `--print-command` exists for: no media, and no ffmpeg needed.

    It must still emit a graph -- refusing here was the regression this branch
    began by fixing -- but it must NOT pretend the result is faithful. The
    omission goes to stderr so a piped chain still receives only the command.
    """
    plan = {
        "plan_format": 1,
        "source": "/nonexistent/video.mp4",
        "operations": [{"op": "audio_replace", "track": "/nonexistent/track.wav"}],
    }
    result = subprocess.run(
        [VID, "render", "out.mp4", "--print-command"], input=json.dumps(plan), capture_output=True, text=True
    )
    assert result.returncode == 0, f"refused a pure compile: {result.stderr}"
    assert result.stdout.startswith("ffmpeg"), f"no command printed: {result.stdout!r}"
    assert "note:" in result.stderr, f"printed an unfaithful command without saying so: {result.stderr!r}"
    # THE NOTE MUST NAME THE FAULT AND THE OMISSION, not merely exist.
    # It used to say "lengths could not be read" for every fallback, and was
    # built from the plan's operation types -- so it named omissions that had
    # not happened. Asserting only that some note appeared would pass either
    # way, which is the blindness this whole file exists to avoid.
    assert "/nonexistent/video.mp4" in result.stderr, (
        f"the note does not name which file is the problem: {result.stderr!r}"
    )
    assert "is not on this machine" in result.stderr, f"the note does not say what is wrong with it: {result.stderr!r}"
    assert "pad and trim to the picture" in result.stderr, (
        f"the note does not say what was actually omitted: {result.stderr!r}"
    )
    assert "not the command `vid render` would run" in result.stderr
    assert "note:" not in result.stdout, "the note must not contaminate the piped command"
    assert not (REPO / "out.mp4").exists()


def test_a_mixed_size_stitch_prints_what_it_runs(clips, tmp_path) -> None:
    """THE SIZE PROBE, which a review proved was untested.

    `concat` needs matching resolution and SAR, so `--fit` resolves a clip that
    is not the edit's size. That resolution needs the SIZE of the other file,
    which only a probe can supply. Dropping the probe in print mode -- exactly
    what #29 did for durations -- makes the printed command exit 234,
    "Conversion failed", while `render` succeeds.

    Every earlier fidelity case stitched clips of identical size, where the
    resolution is a no-op, so the mutation survived with all tests green.
    """
    plan = {
        "plan_format": 1,
        "source": clips["video"],
        "operations": [{"op": "stitch", "sources": [clips["wide"]], "fit": "fill"}],
    }
    rendered, printed, command = _both_ways(plan, tmp_path)
    assert rendered == printed, _mismatch(rendered, printed, command)


def test_a_44k_mono_stitch_with_silence_prints_what_it_runs(clips, tmp_path) -> None:
    """THE AUDIO-FORMAT PROBE, untestable until this fixture existed.

    A silent clip in a stitch gets silence synthesised at the EDIT's format,
    which is read from the source. Every other fixture in this repo is 48 kHz,
    and at 48 kHz "keep the source's format" and "force 48 kHz stereo" emit
    identical graphs -- so dropping the probe changed nothing detectable.

    At 44.1 kHz mono they diverge, which is the same blindness that let the
    original 3 dB format defect ship.
    """
    plan = {
        "plan_format": 1,
        "source": clips["mono441"],
        "operations": [{"op": "stitch", "sources": [clips["silent"]]}],
    }
    rendered, printed, command = _both_ways(plan, tmp_path)
    assert rendered == printed, _mismatch(rendered, printed, command)
    assert "44100" in rendered["audio rate/channels"], (
        f"the fixture is no longer 44.1 kHz, so this test cannot see the defect: {rendered}"
    )


@pytest.mark.parametrize(
    "source",
    ["copy_unsafe_offset", "copy_unsafe_two_streams"],
)
def test_print_command_refuses_a_copy_render_would_refuse(clips, tmp_path, source: str) -> None:
    """M11: `--print-command` must not offer a command `render` will not run.

    `--video-codec copy` cannot retime packets, so `copy_video_duration`
    demands exactly one video stream starting at zero. When it refuses, the
    honest answer is a refusal -- from BOTH paths. A printed command here would
    be the same defect this whole file exists for, one layer down: the flag
    promises "the ffmpeg it would run", and `render` would run nothing.

    THE FIXTURE IS THE POINT. A review found this probe unguarded and could not
    make it diverge, because on an ordinary file `video_duration` and
    `copy_video_duration` return the same number -- they read the same field.
    Skipping the probe was therefore invisible, which is not the same as
    harmless. These two sources are ones where they genuinely disagree:

        video_duration = 3.000000    copy_video_duration = REFUSES

    With the probe skipped in print mode, measured on both fixtures:
        render        rc=1  "requires exactly one video stream starting at zero"
        print-command rc=0  ffmpeg -y -i ... -i ...          <- the divergence
    """
    plan = {
        "plan_format": 1,
        "source": clips[source],
        "operations": [{"op": "audio_replace", "track": clips["long"]}],
    }
    payload = json.dumps(plan)

    rendered = subprocess.run(
        [VID, "render", str(tmp_path / "out.mp4"), "--video-codec", "copy"],
        input=payload,
        capture_output=True,
        text=True,
    )
    printed = subprocess.run(
        [VID, "render", str(tmp_path / "out.mp4"), "--video-codec", "copy", "--print-command"],
        input=payload,
        capture_output=True,
        text=True,
    )

    assert rendered.returncode != 0, (
        f"the fixture is no longer copy-unsafe, so this test proves nothing: {rendered.stdout}"
    )
    assert "exactly one video stream" in rendered.stderr, f"refused for an unrelated reason: {rendered.stderr}"
    assert printed.returncode != 0, (
        "--print-command offered a command for a plan `render` refuses.\n"
        f"  render        rc={rendered.returncode}  {rendered.stderr.strip()[:90]}\n"
        f"  print-command rc={printed.returncode}  {printed.stdout.strip()[:90]}"
    )
    assert "exactly one video stream" in printed.stderr, (
        f"--print-command refused, but not for the copy precondition: {printed.stderr}"
    )
