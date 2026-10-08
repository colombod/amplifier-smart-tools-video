"""Fallback notes must follow independently observed public argv differences.

Use fresh subprocesses, controlled PATHs and real media at one output path.
Check dropped filter families separately: an fps note cannot cover an audio
bound. Equal-argv controls, audio removal and repeated ramp segments prevent
blanket or duplicate notes from masquerading as truthful diagnostics.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import difflib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest

from tests.fixtures import (
    ensure_clips,
    ensure_silent_clip,
    ensure_square_clip,
    frame_colour,
    have_ffmpeg,
    marker_box,
    probe_duration,
    tone_strength,
)
from vid.compile import compile_plan
from vid.plan import AudioMix, AudioRemove, AudioReplace, Overlay, Plan, RampPoint, Retime, Stitch, Trim
from vid.probe import dimensions, frame_rate

REPO = Path(__file__).resolve().parents[1]
VID = str(REPO / ".venv" / "bin" / "vid")

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="drives the real CLI")


def _print(plan: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [VID, "render", "out.mp4", "--print-command"],
        input=json.dumps(plan),
        capture_output=True,
        text=True,
        cwd=REPO,
    )


def test_no_omission_can_still_need_a_duration_assumption() -> None:
    """Trim supplies a cap, not proof of material; removal drops the raw audio dependency.

    Pad/trim remain in the graph, so no omission is claimed. Their nominal
    length still requires a sufficient-material assumption at replacement.
    """
    result = _print(
        {
            "plan_format": 1,
            "source": "/nonexistent/video.mp4",
            "operations": [
                {"op": "audio_remove"},
                {"op": "trim", "start": "0", "end": "2"},
                {"op": "audio_replace", "track": "/nonexistent/track.wav"},
            ],
        }
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ffmpeg"), f"no command printed: {result.stdout!r}"
    assert "omits --" not in result.stderr
    assert "audio replace: sufficient material" in result.stderr


def test_a_plan_that_did_omit_something_still_says_so() -> None:
    """The other direction, so the test above cannot pass by muting all notes."""
    result = _print(
        {
            "plan_format": 1,
            "source": "/nonexistent/video.mp4",
            "operations": [{"op": "audio_replace", "track": "/nonexistent/track.wav"}],
        }
    )
    assert result.returncode == 0, result.stderr
    assert "pad and trim to the picture" in result.stderr, f"a real omission went unreported: {result.stderr!r}"


def test_a_present_but_unusable_source_is_not_called_absent(tmp_path: Path) -> None:
    """Item 4: a directory IS on the machine. Saying otherwise misdirects."""
    directory = tmp_path / "adir.mp4"
    directory.mkdir()
    # The TRACK must exist, or it is the absent path that gets reported and
    # the directory never gets a chance to be described. A genuinely missing
    # file is named first on purpose -- it is the likelier mistake.
    result = _print(
        {
            "plan_format": 1,
            "source": str(directory),
            "operations": [{"op": "audio_replace", "track": "tests/fixtures/meeting.mp4"}],
        }
    )
    assert result.returncode == 0, result.stderr
    assert "is not a readable file" in result.stderr, (
        f"a directory was described as missing from the machine: {result.stderr!r}"
    )
    assert "is not on this machine" not in result.stderr


def test_a_pure_compile_refusal_does_not_say_compile_through_vid_render() -> None:
    """Item 3: the remedy has to be actionable for whoever hit it.

    This caller IS running `vid render`. Telling them to run `vid render`
    reads like a bug in the tool.
    """
    result = _print(
        {
            "plan_format": 1,
            "source": "/nonexistent/a.mp4",
            "operations": [
                {
                    "op": "stitch",
                    "sources": ["/nonexistent/b.mp4"],
                    "transition": "fade",
                    "transition_duration": 0.5,
                }
            ],
        }
    )
    assert result.returncode != 0, "expected this plan to refuse in a pure compile"
    assert "Compile through `vid render`" not in result.stderr, (
        f"told a `vid render` caller to run `vid render`: {result.stderr!r}"
    )
    assert "drop `--print-command`" in result.stderr, f"refused without an actionable remedy: {result.stderr!r}"


def test_the_render_path_remedy_is_unchanged() -> None:
    """A library caller who compiled directly still gets the right advice."""
    from vid.schemas import VidError

    plan = Plan(source="tests/fixtures/talk.mp4").with_operation(AudioReplace(track="tests/fixtures/meeting.mp4"))
    with pytest.raises(VidError, match="Compile through `vid render`"):
        compile_plan(plan, "out.mp4", pure_compile=False)


CASES = (
    "retime",
    "trim_retime",
    "ramp_retime",
    "remove_retime",
    "stitch_retime",
    "retime_mix",
    "equal_stitch",
    "fit_equal_stitch",
    "fit_stitch",
    "fill_stitch",
    "replace",
    "long_overlay",
    "overlay_start_zero",
    "trim_overlay_control",
    "trim_replace_control",
)
CONTROLS = {"trim_overlay_control", "trim_replace_control"}
LIB_PRINT = """
import sys
from vid import lib
from vid.plan import Plan
print(lib.render(Plan.model_validate_json(sys.stdin.read()), sys.argv[1], print_command=True))
"""


@dataclass(frozen=True)
class PrintedPair:
    probed: subprocess.CompletedProcess[str]
    fallback: subprocess.CompletedProcess[str]
    probed_argv: list[str]
    fallback_argv: list[str]
    output: Path
    plan: Plan

    def difference(self) -> str:
        return "\n".join(
            difflib.unified_diff(
                self.probed_argv, self.fallback_argv, fromfile="probed argv", tofile="ffmpeg-only argv"
            )
        )


@pytest.fixture(scope="module")
def real_plans() -> dict[str, Plan]:
    clips = ensure_clips()
    alpha = str(clips["alpha"].path.resolve())
    bravo = str(clips["bravo"].path.resolve())
    charlie = str(clips["charlie"].path.resolve())
    square = str(ensure_square_clip().path.resolve())
    base = Plan(source=alpha)
    return {
        "retime": base.with_operation(Retime(speed=2.0)),
        "trim_retime": base.with_operation(Trim(start=0, end=2)).with_operation(Retime(speed=2.0)),
        "ramp_retime": base.with_operation(Retime(ramp=[RampPoint(at=0, speed=2), RampPoint(at=3, speed=2)])),
        "remove_retime": base.with_operation(AudioRemove()).with_operation(Retime(speed=2)),
        "stitch_retime": base.with_operation(Stitch(sources=[bravo])).with_operation(Retime(speed=2)),
        "retime_mix": base.with_operation(Retime(speed=2)).with_operation(AudioMix(track=bravo)),
        "fit_equal_stitch": base.with_operation(Stitch(sources=[bravo], fit="fit")),
        "overlay_start_zero": Plan(source=charlie).with_operation(Overlay(source=alpha, start=0)),
        "trim_overlay_control": base.with_operation(Trim(start=0, end=2)).with_operation(Overlay(source=bravo)),
        "equal_stitch": base.with_operation(Stitch(sources=[bravo])),
        "fit_stitch": base.with_operation(Stitch(sources=[square], fit="fit")),
        "fill_stitch": base.with_operation(Stitch(sources=[square], fit="fill")),
        "replace": base.with_operation(AudioReplace(track=bravo)),
        "long_overlay": Plan(source=charlie).with_operation(Overlay(source=alpha)),
        "trim_replace_control": base.with_operation(Trim(start=0, end=2)).with_operation(AudioReplace(track=bravo)),
    }


@pytest.fixture(scope="module", params=CASES)
def printed_pair(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory, real_plans: dict[str, Plan]
) -> tuple[str, PrintedPair]:
    case = str(request.param)
    directory = tmp_path_factory.mktemp(case)
    normal = directory / "normal-path"
    ffmpeg_only = directory / "ffmpeg-only-path"
    normal.mkdir()
    ffmpeg_only.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        binary = shutil.which(name)
        assert binary is not None
        (normal / name).symlink_to(binary)
        if name == "ffmpeg":
            (ffmpeg_only / name).symlink_to(binary)
    assert shutil.which("ffprobe", path=str(ffmpeg_only)) is None
    assert shutil.which("ffmpeg", path=str(ffmpeg_only)) is not None
    output = directory / "same-output.mp4"
    plan = real_plans[case]
    assert plan.source is not None
    assert Path(plan.source).is_file()

    def run(path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [VID, "render", str(output), "--print-command"],
            input=plan.model_dump_json(),
            env={**os.environ, "PATH": str(path)},
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=30,
        )

    probed = run(normal)
    fallback = run(ffmpeg_only)
    for result in (probed, fallback):
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("ffmpeg "), result.stdout
    assert not output.exists(), "print_command rendered instead of inspecting"
    probed_argv = shlex.split(probed.stdout)
    fallback_argv = shlex.split(fallback.stdout)
    assert probed_argv[-1] == fallback_argv[-1] == str(output)
    return case, PrintedPair(probed, fallback, probed_argv, fallback_argv, output, plan)


def _graph(argv: list[str]) -> str:
    return argv[argv.index("-filter_complex") + 1]


def test_public_argv_differential_is_independent_of_the_note(printed_pair: tuple[str, PrintedPair]) -> None:
    """Observe the real commands, never the compiler's omissions bookkeeping."""
    case, pair = printed_pair
    if case in CONTROLS:
        assert pair.probed_argv == pair.fallback_argv, pair.difference()
        bound = "apad,atrim=end=2.000000" if case == "trim_replace_control" else "trim=end=2.000000"
        assert bound in _graph(pair.fallback_argv)
        return
    assert pair.probed_argv != pair.fallback_argv, "the fixture no longer exercises a fallback difference"
    changes = {
        "retime": ("fps=30", "apad,atrim=end=1.500000"),
        "trim_retime": ("fps=30",),
        "ramp_retime": ("fps=30",),
        "remove_retime": ("fps=30",),
        "stitch_retime": ("fps=30", "apad,atrim=end=3.000000", "setsar=1"),
        "retime_mix": ("fps=30", "apad,atrim=end=1.500000"),
        "overlay_start_zero": ("trim=end=2.000000",),
        "equal_stitch": ("setsar=1",),
        "fit_equal_stitch": ("setsar=1",),
        "fit_stitch": ("scale=640:360", "pad=640:360", "setsar=1"),
        "fill_stitch": ("scale=640:360", "crop=640:360", "setsar=1"),
        "replace": ("apad,atrim=end=3.000000",),
        "long_overlay": ("trim=end=2.000000",),
    }
    for change in changes[case]:
        pattern = r"(?:^|\]|[,;])" + re.escape(change)
        assert re.search(pattern, _graph(pair.probed_argv)), pair.difference()
        assert not re.search(pattern, _graph(pair.fallback_argv)), pair.difference()
    if case == "trim_retime":
        assert "apad,atrim=end=1.000000" in _graph(pair.fallback_argv), (
            "explicit trim retained the audio bound; only the frame-rate grid is missing"
        )


ClauseKey = tuple[str, tuple[str, ...], bool, str]
AUDIO_BOUND = ("apad", "atrim", "asetpts")


def _expected_clauses(case: str) -> Counter[ClauseKey]:
    grid = ("retime", ("fps",), False, "source frame rate")
    bound = ("retime", AUDIO_BOUND, False, "edit length")
    sar = ("stitch", ("setsar",), False, "clip sizes")
    fit = ("stitch", ("scale", "pad"), True, "clip sizes")
    layer = ("overlay", ("trim", "setpts"), False, "edit length")
    expected: dict[str, Counter[ClauseKey]] = {
        "retime": Counter({grid: 1, bound: 1}),
        "trim_retime": Counter({grid: 1}),
        "ramp_retime": Counter({grid: 1}),
        "remove_retime": Counter({grid: 1}),
        "stitch_retime": Counter({sar: 1, grid: 1, bound: 1}),
        "retime_mix": Counter(
            {
                grid: 1,
                bound: 1,
                ("audio mix", AUDIO_BOUND, False, "edit length"): 3,
                ("audio mix", ("anullsrc", "atrim", "asetpts"), False, "edit length"): 1,
            }
        ),
        "equal_stitch": Counter({sar: 1}),
        "fit_equal_stitch": Counter({sar: 1, fit: 1}),
        "fit_stitch": Counter({sar: 1, fit: 1}),
        "fill_stitch": Counter({sar: 1, ("stitch", ("scale", "crop"), True, "clip sizes"): 1}),
        "replace": Counter({("audio replace", AUDIO_BOUND, False, "edit length"): 1}),
        "long_overlay": Counter({layer: 1}),
        "overlay_start_zero": Counter({layer: 1}),
        "trim_overlay_control": Counter(),
        "trim_replace_control": Counter(),
    }
    if case in {"stitch_retime", "equal_stitch", "fit_equal_stitch", "fit_stitch", "fill_stitch"}:
        expected[case].update(
            {
                ("stitch", AUDIO_BOUND, False, "edit length"): 1,
                ("stitch", AUDIO_BOUND, False, "incoming clip duration"): 1,
            }
        )
    return expected[case]


def _parse_note(stderr: str) -> Counter[ClauseKey]:
    cause, separator, explanation = stderr.strip().partition(", so this command omits -- ")
    assert separator, stderr
    assert cause.startswith("note: "), stderr
    assert re.search(r"ffprobe[^\n]*(?:PATH|installed|missing|unavailable)", cause), cause
    clauses, separator, remedy = explanation.partition(". It is the graph, not the command `vid render` would run. ")
    assert separator, explanation
    assert re.search(r"\b(Install|Restore|Add|Put|Enable)\b[^\n]*ffprobe[^\n]*PATH", remedy), remedy
    assert re.search(r"re-run[^\n]*--print-command", remedy), remedy
    result: Counter[ClauseKey] = Counter()
    clauses = clauses.partition(". this command assumes -- ")[0]
    for clause in clauses.split("; "):
        match = re.fullmatch(
            r"(?P<subject>[a-z]+(?: [a-z]+)*): "
            r"(?P<filters>[a-z][a-z0-9_]*(?:,[a-z][a-z0-9_]*)*)"
            r"(?P<conditional> if clip sizes differ)? "
            r"\((?P<purpose>[^();\n]+), needs (?P<needs>[^();\n]+)\)"
            r"(?: x(?P<count>[2-9]|[1-9][0-9]+))?",
            clause,
        )
        assert match is not None, f"unstructured omission clause: {clause!r}"
        key = (
            match["subject"],
            tuple(match["filters"].split(",")),
            bool(match["conditional"]),
            match["needs"],
        )
        result[key] += int(match["count"] or 1)
    return result


def _assumption_clauses(stderr: str) -> Counter[str]:
    match = re.search(r"(?:this|This) command assumes -- (.*?)\. It is the graph", stderr)
    if not match:
        return Counter()
    result: Counter[str] = Counter()
    for clause in match[1].split("; "):
        text, _, count = clause.partition(" x")
        if "sufficient material" in text:
            assert re.fullmatch(
                r"[a-z ]+: sufficient material exists for the [0-9.]+s nominal edit \([^()]+\)", text
            ), text
            continue
        assert re.fullmatch(
            r"(?:source|stitch|overlay|audio track) input [0-9]+ \(.+\) has (?:an|no) audio stream", text
        ), text
        result[text] += int(count) if count else 1
    return result


def _split_graph(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    quote = False
    escaped = False
    for i, char in enumerate(text):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "'":
            quote = not quote
        elif char in ",;" and not quote:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _filter_instances(argv: list[str]) -> Counter[str]:
    if "-filter_complex" not in argv:
        return Counter()
    return Counter(
        instance
        for part in _split_graph(_graph(argv))
        if (instance := re.sub(r"\[(?:[0-9]+:[av]|[av][0-9]+)\]", "", part))
    )


def _maps(argv: list[str]) -> list[str]:
    return [
        re.sub(r"^([av])[0-9]+$", r"\1", value.strip("[]"))
        for i, value in enumerate(argv)
        if i and argv[i - 1] == "-map"
    ]


def _expected_assumptions(pair: PrintedPair) -> Counter[str]:
    inputs = [value for i, value in enumerate(pair.fallback_argv) if i and pair.fallback_argv[i - 1] == "-i"]
    graph = _graph(pair.fallback_argv) if "-filter_complex" in pair.fallback_argv else ""
    used = set(re.findall(r"\[([0-9]+):a\]", graph))
    used.update(value[:-2] for value in _maps(pair.fallback_argv) if re.fullmatch(r"[0-9]+:a", value))
    roles: dict[str, list[str]] = {}
    for operation in pair.plan.operations:
        if isinstance(operation, Stitch):
            for source in operation.sources:
                if source != "-":
                    roles.setdefault(source, []).append("stitch")
        elif isinstance(operation, Overlay):
            roles.setdefault(operation.source, []).append("overlay")
            if operation.mask and operation.mask.source:
                roles.setdefault(operation.mask.source, []).append("mask")
        elif isinstance(operation, (AudioReplace, AudioMix)):
            roles.setdefault(operation.track, []).append("audio track")
    input_roles = ["source"] + [roles[source].pop(0) for source in inputs[1:]]
    expected: Counter[str] = Counter()
    for index in sorted(used):
        role = input_roles[int(index)]
        expected[f"{role} input {index} ({inputs[int(index)]!r}) has an audio stream"] += 1
    return expected


def _assert_note_matches(case: str, pair: PrintedPair, stderr: str) -> None:
    assert stderr.count("note:") <= 1, stderr
    assumptions = _assumption_clauses(stderr)
    assert assumptions == _expected_assumptions(pair), (assumptions, _expected_assumptions(pair), stderr)
    assert _maps(pair.probed_argv) == _maps(pair.fallback_argv), pair.difference()
    if pair.probed_argv == pair.fallback_argv:
        assert "omits --" not in stderr.lower(), stderr
        assert bool("note:" in stderr) == bool(assumptions or "sufficient material" in stderr), stderr
        return
    clauses = _parse_note(stderr)

    def comparable(argv: list[str]) -> Counter[str]:
        instances = _filter_instances(argv)
        # Clock silence follows the probed join format or the documented fixed
        # fallback; its channel count is not an omitted filter instance.
        for instance, count in list(instances.items()):
            if instance.startswith("anullsrc="):
                instances["anullsrc"] += count
                del instances[instance]
        if case == "retime_mix":
            mix = (
                "amix=inputs=3:duration=longest:normalize=0"
                if argv == pair.probed_argv
                else "amix=inputs=2:duration=first:normalize=0"
            )
            assert instances[mix] == 1, pair.difference()
            instances[mix] -= 1
        if case == "ramp_retime":
            substitutions = (
                ("trim=end_frame=45", "setpts=N/(30*TB)")
                if argv == pair.probed_argv
                else ("trim=end=1.500000", "setpts=PTS-STARTPTS")
            )
            for instance in substitutions:
                assert instances[instance] >= 1, pair.difference()
                instances[instance] -= 1
        return instances

    assert not comparable(pair.fallback_argv) - comparable(pair.probed_argv), pair.difference()
    assert clauses == _expected_clauses(case), (case, clauses, _expected_clauses(case))
    definite: Counter[str] = Counter()
    conditional: Counter[str] = Counter()
    for (_, families, uncertain, _), count in clauses.items():
        for family in families:
            (conditional if uncertain else definite)[family] += count
    probed = comparable(pair.probed_argv)
    fallback = comparable(pair.fallback_argv)
    assert not fallback - probed, f"fallback added filter instances: {fallback - probed}"
    dropped: Counter[str] = Counter()
    for instance, count in (probed - fallback).items():
        dropped[instance.partition("=")[0]] += count
    assert definite <= dropped <= definite + conditional, (definite, dropped, conditional, pair.difference())


def test_public_note_matches_the_actual_argv_difference(printed_pair: tuple[str, PrintedPair]) -> None:
    case, pair = printed_pair
    assert "note:" not in pair.probed.stderr.lower(), pair.probed.stderr
    try:
        _assert_note_matches(case, pair, pair.fallback.stderr)
    except AssertionError as error:
        pytest.fail(f"[note-clause] {error}")


@pytest.mark.parametrize(
    ("printed_pair", "corruption"),
    [
        ("retime_mix", "extra_notice"),
        ("retime_mix", "wrong_subject"),
        ("ramp_retime", "add_count"),
        ("retime_mix", "extra_family"),
        ("retime_mix", "unhandled_family"),
        ("retime_mix", "kitchen_sink"),
        ("retime_mix", "atrim_as_trim"),
    ],
    indirect=["printed_pair"],
)
def test_note_oracle_rejects_corrupted_real_output(
    corruption: str,
    printed_pair: tuple[str, PrintedPair],
) -> None:
    case, pair = printed_pair
    stderr = pair.fallback.stderr
    if corruption == "extra_notice":
        broken = stderr.replace(
            ". It is the graph", "; overlay: trim,setpts (layer length bound, needs edit length). It is the graph"
        )
    elif corruption == "wrong_subject":
        broken = stderr.replace("retime: fps", "stitch: fps")
    elif corruption == "add_count":
        broken = stderr.replace(
            "frame-rate regrid, needs source frame rate)", "frame-rate regrid, needs source frame rate) x6"
        )
    elif corruption == "extra_family":
        broken = stderr.replace("retime: fps", "retime: fps,scale")
    elif corruption == "unhandled_family":
        broken = stderr.replace("retime: fps", "retime: mystery")
    elif corruption == "atrim_as_trim":
        broken = stderr.replace("apad,atrim,asetpts", "apad,trim,asetpts")
    else:
        broken = stderr.replace("retime: fps", "retime: fps,apad,atrim,asetpts,trim,setpts,setsar,scale,pad,crop")
    assert broken != stderr, corruption
    with pytest.raises(AssertionError):
        _assert_note_matches(case, pair, broken)


@pytest.mark.parametrize("printed_pair", sorted(CONTROLS), indirect=True)
def test_equal_argv_oracle_rejects_false_notice(printed_pair: tuple[str, PrintedPair]) -> None:
    case, pair = printed_pair
    with pytest.raises(AssertionError):
        _assert_note_matches(case, pair, pair.fallback.stderr + "note: falsely reported omission\n")


@pytest.mark.parametrize("printed_pair", [case for case in CASES if case not in CONTROLS], indirect=True)
def test_note_oracle_accepts_purpose_paraphrase(printed_pair: tuple[str, PrintedPair]) -> None:
    case, pair = printed_pair
    paraphrased = re.sub(r"\([^();\n]+, needs ", "(the omitted timing or sizing step, needs ", pair.fallback.stderr)
    assert paraphrased != pair.fallback.stderr
    _assert_note_matches(case, pair, paraphrased)


def test_probed_printed_argv_renders_the_requested_edit(printed_pair: tuple[str, PrintedPair]) -> None:
    case, pair = printed_pair
    result = subprocess.run(pair.probed_argv, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    duration = probe_duration(pair.output)
    expected = {
        "retime": 1.5,
        "trim_retime": 1.0,
        "ramp_retime": 1.5,
        "remove_retime": 1.5,
        "stitch_retime": 3.0,
        "retime_mix": 1.5,
        "overlay_start_zero": 2.0,
        "trim_overlay_control": 2.0,
        "equal_stitch": 6.0,
        "fit_equal_stitch": 6.0,
        "fit_stitch": 5.0,
        "fill_stitch": 5.0,
        "replace": 3.0,
        "long_overlay": 2.0,
        "trim_replace_control": 2.0,
    }
    assert duration == pytest.approx(expected[case], abs=0.15), f"{case} rendered {duration}s"
    assert dimensions(str(pair.output)) == (640, 360)
    if case in {"equal_stitch", "fit_equal_stitch"}:
        early = frame_colour(pair.output, 0.5)
        late = frame_colour(pair.output, 4.0)
        assert early[0] > early[1], early
        assert late[1] > late[0], late
    if case == "fit_stitch":
        width, height = marker_box(pair.output, 4.0, 640, 360)
        assert width == pytest.approx(90, abs=6), (width, height)
        assert height == pytest.approx(90, abs=6), (width, height)
    if case == "trim_replace_control":
        replacement = tone_strength(pair.output, 660)
        original = tone_strength(pair.output, 440)
        assert replacement > 10 * original, (replacement, original)


@pytest.mark.parametrize("case", ["fit_stitch", "long_overlay"])
def test_fallback_argv_has_a_measured_consequence(case: str, tmp_path: Path, real_plans: dict[str, Plan]) -> None:
    """These missing filters change delivery, not just the spelling of a graph."""
    ffmpeg_only = tmp_path / "ffmpeg-only"
    ffmpeg_only.mkdir()
    binary = shutil.which("ffmpeg")
    assert binary is not None
    (ffmpeg_only / "ffmpeg").symlink_to(binary)
    output = tmp_path / "fallback.mp4"
    printed = subprocess.run(
        [sys.executable, "-c", LIB_PRINT, str(output)],
        input=real_plans[case].model_dump_json(),
        env={**os.environ, "PATH": str(ffmpeg_only)},
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert printed.returncode == 0, printed.stderr
    result = subprocess.run(shlex.split(printed.stdout), capture_output=True, text=True, timeout=60)
    if case == "fit_stitch":
        assert result.returncode != 0, "the missing size normalization unexpectedly rendered successfully"
        assert "640x360" in result.stderr, result.stderr
        assert "480x480" in result.stderr, result.stderr
    else:
        assert result.returncode == 0, result.stderr
        duration = probe_duration(output)
        assert duration == pytest.approx(3.0, abs=0.15), f"unbounded layer rendered {duration}s"
        source = real_plans[case].source
        assert source is not None
        assert duration > probe_duration(source) + 0.8


def test_non_pure_compile_never_records_fallback_omissions(real_plans: dict[str, Plan]) -> None:
    for case in ("retime", "remove_retime", "equal_stitch", "long_overlay"):
        omissions: list[str] = []
        plan = real_plans[case]
        assert plan.source
        compile_plan(
            plan,
            "out.mp4",
            durations={
                path: probe_duration(path)
                for path in [plan.source, *[s for op in plan.operations if isinstance(op, Stitch) for s in op.sources]]
            },
            pure_compile=False,
            omissions=omissions,
        )
        assert omissions == [], (case, omissions)


def test_fallback_with_known_rate_and_explicit_length_has_no_note(tmp_path: Path, real_plans: dict[str, Plan]) -> None:
    # An unavailable track selects fallback, but fps is still probed from the
    # real picture and trim supplies every length. No bound/grid was omitted.
    plan = real_plans["trim_retime"].with_operation(AudioReplace(track=str(tmp_path / "missing.wav")))
    result = subprocess.run(
        [sys.executable, "-c", LIB_PRINT, str(tmp_path / "out.mp4")],
        input=plan.model_dump_json(),
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    graph = _graph(shlex.split(result.stdout))
    assert "fps=30" in graph
    assert "apad,atrim=end=1.000000" in graph
    assert "sufficient material" in result.stderr, result.stderr
    assert "omits --" not in result.stderr, result.stderr


def test_known_metadata_leaves_real_compile_argv_unchanged(real_plans: dict[str, Plan]) -> None:
    # The mode may change diagnostics, never the command when facts are supplied.
    for plan in real_plans.values():
        paths = [plan.source]
        paths += [source for op in plan.operations if isinstance(op, Stitch) for source in op.sources]
        paths += [op.source for op in plan.operations if isinstance(op, Overlay)]
        existing = [path for path in paths if path is not None]
        durations = {path: probe_duration(path) for path in existing}
        sizes = {path: size for path in existing if (size := dimensions(path)) is not None}
        omissions: list[str] = []
        assert plan.source is not None
        rate = frame_rate(plan.source)
        real = compile_plan(
            plan,
            "out.mp4",
            durations=durations,
            frame_rate=rate,
            source_sizes=sizes,
            pure_compile=False,
            omissions=omissions,
        )
        fallback = compile_plan(
            plan,
            "out.mp4",
            durations=durations,
            frame_rate=rate,
            source_sizes=sizes,
            pure_compile=True,
            omissions=omissions,
        )
        assert real == fallback
        assert omissions == []


def _show(plan: Plan, output: Path, path: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [VID, "render", str(output), "--print-command"],
        input=plan.model_dump_json(),
        env={**os.environ, **({"PATH": path} if path is not None else {})},
        capture_output=True,
        text=True,
        timeout=30,
        cwd=REPO,
    )


@pytest.fixture
def ffmpeg_path(tmp_path: Path) -> str:
    directory = tmp_path / "only-ffmpeg"
    directory.mkdir()
    binary = shutil.which("ffmpeg")
    assert binary
    (directory / "ffmpeg").symlink_to(binary)
    return str(directory)


@pytest.mark.parametrize("verb", ["replace", "overlay", "retime"])
def test_trim_stitch_unknown_length_never_bounds_to_old_trim(
    verb: str,
    real_plans: dict[str, Plan],
    tmp_path: Path,
    ffmpeg_path: str,
) -> None:
    base = real_plans["equal_stitch"]
    assert base.source
    assert isinstance(base.operations[0], Stitch)
    incoming = base.operations[0].sources[0]
    plan = Plan(source=base.source).with_operation(Trim(start=0, end=2)).with_operation(Stitch(sources=[incoming]))
    operation = {
        "replace": AudioReplace(track=incoming),
        "overlay": Overlay(source=incoming),
        "retime": Retime(speed=2),
    }[verb]
    plan = plan.with_operation(operation)
    output = tmp_path / "bound.mp4"
    probed, fallback = _show(plan, output), _show(plan, output, ffmpeg_path)
    assert probed.returncode == fallback.returncode == 0, (probed.stderr, fallback.stderr)
    pg, fg = _graph(shlex.split(probed.stdout)), _graph(shlex.split(fallback.stdout))
    expected = 2.5 if verb == "retime" else 5.0
    bound = ("trim" if verb == "overlay" else "atrim") + f"=end={expected:.6f}"
    assert bound in pg
    assert "atrim=end=1.000000" not in fg
    assert fg.count("atrim=end=2.000000") == 1  # Only the running stitch input retains the trim cap.
    if verb == "overlay":
        assert "trim=start=0.0:end=2.0" in fg
        assert not re.search(r"(?:^|\]|[,;])trim=end=", fg)
    assert "needs edit length" in fallback.stderr
    result = subprocess.run(shlex.split(probed.stdout), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert streams.returncode == 0, streams.stderr
    lengths = {s["codec_type"]: float(s["duration"]) for s in json.loads(streams.stdout)["streams"]}
    assert lengths["video"] == pytest.approx(expected, abs=0.1)
    assert lengths["audio"] == pytest.approx(expected, abs=0.1)


@pytest.mark.parametrize("case", ["vignette", "retime", "stitch", "remove", "overlay_keep"])
def test_silent_fallback_discloses_used_audio_prerequisite(
    case: str,
    real_plans: dict[str, Plan],
    tmp_path: Path,
    ffmpeg_path: str,
) -> None:
    from vid.plan import LayerAudio, Vignette

    silent = str(ensure_silent_clip().path.resolve())
    alpha = real_plans["retime"].source
    assert alpha
    plan = {
        "vignette": Plan(source=silent).with_operation(Vignette()),
        "retime": Plan(source=silent).with_operation(Retime(speed=2)),
        "stitch": Plan(source=alpha).with_operation(Stitch(sources=[silent])),
        "remove": Plan(source=silent).with_operation(AudioRemove()).with_operation(Vignette()),
        "overlay_keep": Plan(source=alpha)
        .with_operation(Trim(start=0, end=2))
        .with_operation(Overlay(source=silent, audio=LayerAudio(policy="keep"))),
    }[case]
    output = tmp_path / "silent.mp4"
    fallback = _show(plan, output, ffmpeg_path)
    assert fallback.returncode == 0, fallback.stderr
    assumptions = _assumption_clauses(fallback.stderr)
    if case == "remove":
        assert not assumptions
        assert "note:" not in fallback.stderr
    else:
        role, index, source = (
            ("overlay", 1, silent)
            if case == "overlay_keep"
            else ("stitch", 1, silent)
            if case == "stitch"
            else ("source", 0, silent)
        )
        assert assumptions[f"{role} input {index} ({source!r}) has an audio stream"] == 1, fallback.stderr
    result = subprocess.run(shlex.split(fallback.stdout), capture_output=True, text=True, timeout=60)
    assert (result.returncode == 0) == (case == "remove"), result.stderr
    if case == "overlay_keep":
        probed = _show(plan, output)
        assert probed.returncode != 0
        assert "carries no audio stream" in probed.stderr
    else:
        probed = _show(plan, output)
        assert probed.returncode == 0, probed.stderr
        result = subprocess.run(shlex.split(probed.stdout), capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        assert probe_duration(output) == pytest.approx(
            {"vignette": 2, "retime": 1, "stitch": 5, "remove": 2}[case], abs=0.15
        )


def test_missing_stitch_follows_known_silent_source(tmp_path: Path) -> None:
    silent = str(ensure_silent_clip().path.resolve())
    missing = str(tmp_path / "missing.mp4")
    plan = Plan(source=silent).with_operation(Stitch(sources=[missing]))
    printed = _show(plan, tmp_path / "out.mp4")
    assert printed.returncode == 0, printed.stderr
    assert _assumption_clauses(printed.stderr) == Counter({f"stitch input 1 ({missing!r}) has no audio stream": 1})
    assert "[1:a]" not in _graph(shlex.split(printed.stdout))


def test_partial_durations_refuse_and_full_durations_render(real_plans: dict[str, Plan], tmp_path: Path) -> None:
    from vid.schemas import VidError

    base = real_plans["equal_stitch"]
    assert base.source
    assert isinstance(base.operations[0], Stitch)
    incoming = base.operations[0].sources[0]
    plan = (
        Plan(source=base.source)
        .with_operation(Trim(start=0, end=2))
        .with_operation(Stitch(sources=[incoming]))
        .with_operation(AudioReplace(track=incoming))
    )
    with pytest.raises(VidError, match="duration"):
        compile_plan(plan, str(tmp_path / "partial.mp4"), durations={base.source: 3})
    argv = compile_plan(plan, str(tmp_path / "full.mp4"), durations={base.source: 3, incoming: 3})
    assert "atrim=end=5.000000" in _graph(argv)
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert probe_duration(tmp_path / "full.mp4") == pytest.approx(5, abs=0.1)


@pytest.mark.parametrize("mode", ["audio", "silent", "transition"])
def test_each_stitch_elapsed_site_propagates_unknown(mode: str, real_plans: dict[str, Plan]) -> None:
    from vid.schemas import VidError

    base = real_plans["equal_stitch"]
    assert base.source
    assert isinstance(base.operations[0], Stitch)
    incoming = base.operations[0].sources[0]
    join = Stitch(sources=[incoming], transition="fade" if mode == "transition" else None)
    plan = (
        Plan(source=base.source)
        .with_operation(Trim(start=0, end=2))
        .with_operation(join)
        .with_operation(AudioReplace(track=incoming))
    )
    with pytest.raises(VidError, match="duration"):
        compile_plan(plan, "out.mp4", durations={base.source: 3}, has_audio=mode != "silent")


@pytest.mark.parametrize("site", ["zoom", "animated", "incoming", "running"])
def test_probe_remedy_exact_sites(site: str, real_plans: dict[str, Plan]) -> None:
    from vid.plan import Motion, Zoom
    from vid.schemas import VidError

    base = real_plans["retime"]
    assert base.source
    join = real_plans["equal_stitch"].operations[0]
    assert isinstance(join, Stitch)
    incoming = join.sources[0]
    operations = {
        "zoom": Zoom(to=2),
        "animated": Overlay(source=incoming, motion=Motion(start=0, duration=1, to_x=5)),
        "incoming": Stitch(sources=[incoming]),
        "running": Stitch(sources=[incoming]),
    }
    plan = Plan(source=base.source).with_operation(operations[site])
    with pytest.raises(VidError) as error:
        compile_plan(
            plan, "out.mp4", pure_compile=True, has_audio=site != "running", source_audio={incoming: site == "running"}
        )
    assert "drop `--print-command`" in str(error.value)
    assert "through `vid render`" not in str(error.value)


@pytest.mark.parametrize("corruption", ["missing_assumption", "wrong_map", "wrong_bound"])
@pytest.mark.parametrize("printed_pair", ["trim_replace_control"], indirect=True)
def test_exact_oracle_rejects_numeric_and_stream_corruption(
    corruption: str,
    printed_pair: tuple[str, PrintedPair],
) -> None:
    from dataclasses import replace

    case, pair = printed_pair
    stderr = pair.fallback.stderr
    argv = pair.fallback_argv.copy()
    if corruption == "missing_assumption":
        stderr = ""
    elif corruption == "wrong_map":
        argv[argv.index("-map") + 1] = "1:a"
    else:
        argv[argv.index("-filter_complex") + 1] = _graph(argv).replace("atrim=end=2.000000", "atrim=end=5.000000")
    with pytest.raises(AssertionError):
        _assert_note_matches(case, replace(pair, fallback_argv=argv), stderr)


def test_filter_split_preserves_quoted_and_escaped_commas() -> None:
    assert _split_graph("[0:v]scale=w='if(gt(t,1),4,2)':h=2[v1];[v1]crop=x=1\\,2[v2]") == [
        "[0:v]scale=w='if(gt(t,1),4,2)':h=2[v1]",
        "[v1]crop=x=1\\,2[v2]",
    ]


@pytest.mark.parametrize("verb", ["retime", "overlay"])
def test_partial_stitch_metadata_refuses_downstream_bounds(verb: str, real_plans: dict[str, Plan]) -> None:
    from vid.schemas import VidError

    base = real_plans["equal_stitch"]
    assert base.source
    assert isinstance(base.operations[0], Stitch)
    incoming = base.operations[0].sources[0]
    plan = Plan(source=base.source).with_operation(Trim(start=0, end=2)).with_operation(Stitch(sources=[incoming]))
    plan = plan.with_operation(Retime(speed=2) if verb == "retime" else Overlay(source=incoming))
    with pytest.raises(VidError, match="incoming audio length bound"):
        compile_plan(plan, "out.mp4", durations={base.source: 3})


def test_no_assumption_for_removed_or_replaced_unused_source(tmp_path: Path, ffmpeg_path: str) -> None:
    clips = ensure_clips()
    track = str(clips["bravo"].path.resolve())
    source = str(tmp_path / "absent.mp4")
    for operation in (AudioRemove(), AudioReplace(track=track)):
        plan = Plan(source=source).with_operation(operation)
        printed = _show(plan, tmp_path / "out.mp4", ffmpeg_path)
        assert printed.returncode == 0, printed.stderr
        assumptions = _assumption_clauses(printed.stderr)
        assert not any(clause.startswith("source input") for clause in assumptions)


@pytest.mark.parametrize("case", ["vignette", "trim_replace_control"])
def test_assumptions_only_equal_argv_still_warns(case: str, tmp_path: Path, ffmpeg_path: str) -> None:
    from vid.plan import Vignette

    clips = ensure_clips()
    source = str(clips["alpha"].path.resolve())
    track = str(clips["bravo"].path.resolve())
    plan = (
        Plan(source=source).with_operation(Vignette())
        if case == "vignette"
        else Plan(source=source).with_operation(Trim(start=0, end=2)).with_operation(AudioReplace(track=track))
    )
    probed = _show(plan, tmp_path / "out.mp4")
    fallback = _show(plan, tmp_path / "out.mp4", ffmpeg_path)
    assert probed.returncode == 0, probed.stderr
    assert fallback.returncode == 0, fallback.stderr
    assert probed.stdout == fallback.stdout
    assert "omits --" not in fallback.stderr
    duration_clause = (
        "audio replace: sufficient material exists for the 2s nominal edit (track pad and trim to the picture); "
        if case == "trim_replace_control"
        else ""
    )
    audio_clause = (
        f"audio track input 1 ({track!r}) has an audio stream; source input 0 ({source!r}) has an audio stream"
        if case == "trim_replace_control"
        else f"source input 0 ({source!r}) has an audio stream"
    )
    assert fallback.stderr == (
        "note: ffprobe is not on PATH, so this command assumes -- "
        + duration_clause
        + audio_clause
        + ". It is the graph; the command `vid render` would run may match if these assumptions hold. "
        "Install ffprobe or restore it on PATH, then re-run `--print-command` with the media available.\n"
    )
    assert _assumption_clauses(fallback.stderr) == Counter(dict.fromkeys(audio_clause.split("; "), 1))


def test_non_pure_compile_records_actual_unknown_dependencies(real_plans: dict[str, Plan]) -> None:
    assumptions: list[str] = []
    plan = real_plans["equal_stitch"]
    assert plan.source
    assert isinstance(plan.operations[0], Stitch)
    durations = {path: probe_duration(path) for path in [plan.source, *plan.operations[0].sources]}
    compile_plan(plan, "out.mp4", durations=durations, has_audio=None, assumptions=assumptions)
    assert len(assumptions) == 2
    assert all("has an audio stream" in item for item in assumptions)


def test_audio_presence_reports_real_unknown_and_preserves_legacy(tmp_path: Path, ffmpeg_path: str) -> None:
    from vid.probe import audio_presence, has_audio

    alpha = str(ensure_clips()["alpha"].path.resolve())
    silent = str(ensure_silent_clip().path.resolve())
    missing = str(tmp_path / "missing.mp4")
    assert audio_presence(alpha) is True
    assert audio_presence(silent) is False
    assert audio_presence(missing) is None
    assert has_audio(missing) is True
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from vid.probe import audio_presence, has_audio; import sys; print(repr(audio_presence(sys.argv[1])), has_audio(sys.argv[1]))",
            alpha,
        ],
        env={**os.environ, "PATH": ffmpeg_path},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "None True"


def test_known_incoming_duration_cannot_recover_unknown_prior_edit(real_plans: dict[str, Plan]) -> None:
    base = real_plans["equal_stitch"]
    assert isinstance(base.operations[0], Stitch)
    incoming = base.operations[0].sources[0]
    plan = base.with_operation(AudioReplace(track=incoming))
    omissions: list[str] = []
    argv = compile_plan(plan, "out.mp4", durations={incoming: 3}, pure_compile=True, omissions=omissions)
    assert _graph(argv).count("atrim=end=3.000000") == 1  # Incoming clip only, never the replacement.
    assert "track pad and trim to the picture" in ";".join(omissions)
    assert any("audio replace:" in item for item in omissions)


@pytest.mark.parametrize("case", ["silent_multistitch", "source_unknown"])
def test_transition_requires_known_running_elapsed(case: str, tmp_path: Path) -> None:
    from vid.schemas import VidError

    clips = ensure_clips()
    alpha, bravo, charlie = (str(clips[name].path.resolve()) for name in ("alpha", "bravo", "charlie"))
    if case == "silent_multistitch":
        plan = (
            Plan(source=alpha)
            .with_operation(AudioRemove())
            .with_operation(Trim(start=0, end=2))
            .with_operation(Stitch(sources=[bravo], transition="fade", transition_duration=0.5))
            .with_operation(Stitch(sources=[charlie], transition="fade", transition_duration=0.5))
        )
        partial = {alpha: probe_duration(alpha), charlie: probe_duration(charlie)}
    else:
        plan = Plan(source=alpha).with_operation(Stitch(sources=[bravo], transition="fade", transition_duration=0.5))
        partial = {bravo: probe_duration(bravo)}
    with pytest.raises(VidError, match=r"transition.*(?:running edit|incoming clip).*duration") as error:
        compile_plan(plan, str(tmp_path / "partial.mp4"), durations=partial)
    assert "Compile through `vid render`, which probes it." in str(error.value)


@pytest.mark.parametrize("case", ["silent_multistitch", "source_unknown"])
def test_transition_full_facts_render_counterpart(case: str, tmp_path: Path) -> None:
    clips = ensure_clips()
    alpha, bravo, charlie = (str(clips[name].path.resolve()) for name in ("alpha", "bravo", "charlie"))
    plan = (
        Plan(source=alpha)
        .with_operation(AudioRemove())
        .with_operation(Trim(start=0, end=2))
        .with_operation(Stitch(sources=[bravo], transition="fade", transition_duration=0.5))
        .with_operation(Stitch(sources=[charlie], transition="fade", transition_duration=0.5))
        if case == "silent_multistitch"
        else Plan(source=alpha).with_operation(Stitch(sources=[bravo], transition="fade", transition_duration=0.5))
    )
    output = tmp_path / "full.mp4"
    facts = {path: probe_duration(path) for path in (alpha, bravo, charlie)}
    argv = compile_plan(plan, str(output), durations=facts)
    assert "offset=4.0" in _graph(argv) if case == "silent_multistitch" else "offset=2.5" in _graph(argv)
    rendered = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, timeout=60)
    assert rendered.returncode == 0, rendered.stderr
    measured = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert measured.returncode == 0, measured.stderr
    lengths = {s["codec_type"]: float(s["duration"]) for s in json.loads(measured.stdout)["streams"]}
    expected = 6 if case == "silent_multistitch" else 5.5
    assert lengths["video"] == pytest.approx(expected, abs=0.1)
    if case == "silent_multistitch":
        assert "audio" not in lengths
    else:
        assert lengths["audio"] == pytest.approx(expected, abs=0.1)


def test_mixed_stitch_overlay_assumptions_label_actual_inputs(tmp_path: Path, ffmpeg_path: str) -> None:
    from vid.plan import LayerAudio

    clips = ensure_clips()
    alpha, bravo, charlie = (str(clips[name].path.resolve()) for name in ("alpha", "bravo", "charlie"))
    plan = (
        Plan(source=alpha)
        .with_operation(Stitch(sources=[bravo]))
        .with_operation(Trim(start=0, end=2))
        .with_operation(Overlay(source=charlie, audio=LayerAudio(policy="keep")))
    )
    output = tmp_path / "mixed.mp4"
    probed, fallback = _show(plan, output), _show(plan, output, ffmpeg_path)
    assert probed.returncode == fallback.returncode == 0, (probed.stderr, fallback.stderr)
    pair = PrintedPair(probed, fallback, shlex.split(probed.stdout), shlex.split(fallback.stdout), output, plan)
    expected = Counter(
        {
            f"source input 0 ({alpha!r}) has an audio stream": 1,
            f"stitch input 1 ({bravo!r}) has an audio stream": 1,
            f"overlay input 2 ({charlie!r}) has an audio stream": 1,
        }
    )
    assert _expected_assumptions(pair) == expected
    assert _assumption_clauses(fallback.stderr) == expected
    _assert_note_matches("equal_stitch", pair, fallback.stderr)


def test_bare_silent_auto_selection_is_not_an_audio_dependency(tmp_path: Path, ffmpeg_path: str) -> None:
    plan = Plan(source=str(ensure_silent_clip().path.resolve()))
    printed = _show(plan, tmp_path / "bare.mp4", ffmpeg_path)
    assert printed.returncode == 0, printed.stderr
    argv = shlex.split(printed.stdout)
    pair = PrintedPair(printed, printed, argv, argv, tmp_path / "bare.mp4", plan)
    assert _expected_assumptions(pair) == Counter()
    assert "note:" not in printed.stderr
    executed = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert executed.returncode == 0, executed.stderr
    measured = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(tmp_path / "bare.mp4")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert measured.returncode == 0, measured.stderr
    assert [s["codec_type"] for s in json.loads(measured.stdout)["streams"]] == ["video"]


def test_damaged_source_explicit_audio_map_discloses_unknown(tmp_path: Path) -> None:
    from vid.plan import Vignette

    damaged = tmp_path / "damaged.mp4"
    damaged.write_bytes(ensure_clips()["alpha"].path.read_bytes()[:256])
    printed = _show(Plan(source=str(damaged)).with_operation(Vignette()), tmp_path / "out.mp4")
    assert printed.returncode == 0, printed.stderr
    assert "0:a" in shlex.split(printed.stdout)
    assert "required metadata could not be read" in printed.stderr
    assert f"source input 0 ({str(damaged)!r}) has an audio stream" in printed.stderr


@pytest.mark.parametrize("start", [0, 1, 8])
@pytest.mark.parametrize("operation", [AudioMix, AudioReplace])
def test_positive_start_clamp_is_a_numeric_omission(start: float, operation, tmp_path: Path, ffmpeg_path: str) -> None:
    clips = ensure_clips()
    source, track = (str(clips[name].path.resolve()) for name in ("alpha", "bravo"))
    plan = Plan(source=source).with_operation(operation(track=track, start=start))
    output = tmp_path / "same.mp4"
    probed, fallback = _show(plan, output), _show(plan, output, ffmpeg_path)
    assert probed.returncode == 0, probed.stderr
    assert fallback.returncode == 0, fallback.stderr
    pg, fg = _graph(shlex.split(probed.stdout)), _graph(shlex.split(fallback.stdout))
    assert f"adelay={min(start, 3) * 1000:.6f}:all=1" in pg
    assert f"adelay={start * 1000:.6f}:all=1" in fg
    assert ("adelay (start clamp to the picture, needs edit length)" in fallback.stderr) == (start > 0)


@pytest.mark.parametrize("silent", ["no", "removed", "natural"])
@pytest.mark.parametrize("endpoint", [3, 8])
@pytest.mark.parametrize("consumer", ["replace", "overlay"])
def test_ramp_actual_picture_frames_and_audio_match(
    silent: str, endpoint: float, consumer: str, tmp_path: Path
) -> None:
    from vid.plan import LayerAudio

    clips = ensure_clips()
    source = str((ensure_silent_clip() if silent == "natural" else clips["alpha"]).path.resolve())
    track = str(clips["bravo"].path.resolve())
    plan = Plan(source=source)
    if silent == "removed":
        plan = plan.with_operation(AudioRemove())
    plan = plan.with_operation(Retime(ramp=[RampPoint(at=0, speed=2), RampPoint(at=endpoint, speed=2)]))
    plan = plan.with_operation(
        AudioReplace(track=track) if consumer == "replace" else Overlay(source=track, audio=LayerAudio(policy="keep"))
    )
    output = tmp_path / "ramp.mp4"
    printed = _show(plan, output)
    assert printed.returncode == 0, printed.stderr
    executed = subprocess.run(shlex.split(printed.stdout), capture_output=True, text=True, timeout=60)
    assert executed.returncode == 0, executed.stderr
    measured = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_frames", "-of", "json", str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert measured.returncode == 0, measured.stderr
    data = json.loads(measured.stdout)
    streams = {s["codec_type"]: s for s in data["streams"]}
    expected = 1.0 if silent == "natural" else 1.5
    frames = [f for f in data["frames"] if f["media_type"] == "video"]
    assert len(frames) == int(expected * 30)
    assert float(streams["video"]["duration"]) == expected
    assert float(frames[-1]["best_effort_timestamp_time"]) + 1 / 30 == pytest.approx(expected, abs=1e-6)
    assert float(streams["audio"]["duration"]) == pytest.approx(
        expected, abs=1024 / int(streams["audio"]["sample_rate"])
    )


@pytest.mark.parametrize(("operation", "expected"), [(Trim(start=0, end=8), 3), (Trim(start=0, end=2), 2)])
def test_trim_replacement_uses_material_not_endpoint(operation: Trim, expected: float, tmp_path: Path) -> None:
    clips = ensure_clips()
    source, track = (str(clips[name].path.resolve()) for name in ("alpha", "bravo"))
    printed = _show(
        Plan(source=source).with_operation(operation).with_operation(AudioReplace(track=track)), tmp_path / "trim.mp4"
    )
    assert printed.returncode == 0, printed.stderr
    assert f"atrim=end={expected:.6f}" in _graph(shlex.split(printed.stdout))
    executed = subprocess.run(shlex.split(printed.stdout), capture_output=True, text=True, timeout=60)
    assert executed.returncode == 0, executed.stderr
    measured = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(tmp_path / "trim.mp4")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert measured.returncode == 0, measured.stderr
    streams = {s["codec_type"]: s for s in json.loads(measured.stdout)["streams"]}
    assert float(streams["video"]["duration"]) == expected
    assert float(streams["audio"]["duration"]) == pytest.approx(
        expected, abs=1024 / int(streams["audio"]["sample_rate"])
    )


@pytest.mark.parametrize(
    "retime", [None, Retime(speed=2), Retime(ramp=[RampPoint(at=0, speed=2), RampPoint(at=8, speed=2)])]
)
def test_unknown_trim_is_only_a_cap_at_consumer(retime: Retime | None) -> None:
    from vid.schemas import VidError

    plan = Plan(source="unknown.mp4").with_operation(Trim(start=0, end=2))
    if retime:
        plan = plan.with_operation(retime)
    plan = plan.with_operation(AudioReplace(track="track.wav"))
    with pytest.raises(VidError, match="known positive video duration"):
        compile_plan(plan, "out.mp4")
    assumptions: list[str] = []
    argv = compile_plan(plan, "out.mp4", pure_compile=True, assumptions=assumptions)
    expected = 1 if retime else 2
    assert f"atrim=end={expected:.6f}" in _graph(argv)
    assert any("audio replace" in item and "sufficient material" in item for item in assumptions)


@pytest.mark.parametrize("role", ["overlay", "stitch"])
def test_secondary_unknown_audio_retains_tristate(role: str, tmp_path: Path, ffmpeg_path: str) -> None:
    from vid.plan import LayerAudio

    clips = ensure_clips()
    source = str(clips["alpha"].path.resolve())
    damaged = tmp_path / "damaged.mp4"
    damaged.write_bytes(clips["bravo"].path.read_bytes()[:256])
    if role == "overlay":
        plan = Plan(source=source).with_operation(Overlay(source=str(damaged), audio=LayerAudio(policy="keep")))
        printed = _show(plan, tmp_path / "out.mp4")
        assert printed.returncode == 0, printed.stderr
        assert f"overlay input 1 ({str(damaged)!r}) has an audio stream" in printed.stderr
        assert "required metadata could not be read" in printed.stderr
    else:
        assumptions: list[str] = []
        plan = Plan(source=source).with_operation(Stitch(sources=[str(damaged), source]))
        argv = compile_plan(
            plan, "out.mp4", source_audio={str(damaged): None, source: None}, assumptions=assumptions, pure_compile=True
        )
        assert "[1:a]" in _graph(argv)
        assert f"stitch input 1 ({str(damaged)!r}) has an audio stream" in assumptions
        assert f"stitch input 2 ({source!r}) has an audio stream" in assumptions
        printed = _show(plan, tmp_path / "fallback.mp4", ffmpeg_path)
        assert printed.returncode == 0, printed.stderr
        assert f"stitch input 1 ({str(damaged)!r}) has an audio stream" in printed.stderr


def test_stitch_audio_fact_is_probed_even_when_another_input_is_missing(tmp_path: Path) -> None:
    source = str(ensure_clips()["alpha"].path.resolve())
    missing = str(tmp_path / "missing.mp4")
    printed = _show(Plan(source=source).with_operation(Stitch(sources=[missing])), tmp_path / "out.mp4")
    assert printed.returncode == 0, printed.stderr
    assert f"stitch input 1 ({missing!r}) has an audio stream" in printed.stderr
    assert f"source input 0 ({source!r}) has an audio stream" not in printed.stderr


def test_raw_sink_remains_a_mandatory_audio_dependency() -> None:
    from vid.compile import Compiler

    compiler = Compiler(Plan(source="unknown.mp4"), has_audio=None)
    compiler.audio = None
    compiler.filters.append("[0:a]anullsink")
    compiler.record_audio_dependencies(["ffmpeg", "-an", "out.mp4"])
    assert compiler.assumptions == ["source input 0 ('unknown.mp4') has an audio stream"]


def test_copy_unknown_audio_explicit_map_records_assumption(tmp_path: Path) -> None:
    source = str(ensure_clips()["alpha"].path.resolve())
    assumptions: list[str] = []
    argv = compile_plan(
        Plan(source=source),
        str(tmp_path / "copy.mp4"),
        video_codec="copy",
        durations={source: 3},
        has_audio=None,
        assumptions=assumptions,
    )
    assert "0:a" in argv
    assert assumptions == [f"source input 0 ({source!r}) has an audio stream"]


def test_cut_interval_is_intersected_with_real_material(tmp_path: Path) -> None:
    from vid.plan import Cut

    clips = ensure_clips()
    source, track = (str(clips[name].path.resolve()) for name in ("alpha", "bravo"))
    plan = Plan(source=source).with_operation(Cut(start=1, end=8)).with_operation(AudioReplace(track=track))
    printed = _show(plan, tmp_path / "cut.mp4")
    assert printed.returncode == 0, printed.stderr
    assert "atrim=end=1.000000" in _graph(shlex.split(printed.stdout))
    executed = subprocess.run(shlex.split(printed.stdout), capture_output=True, text=True, timeout=60)
    assert executed.returncode == 0, executed.stderr
    assert probe_duration(tmp_path / "cut.mp4") == pytest.approx(1, abs=1024 / 44100)


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), 0, -1])
def test_invalid_duration_cannot_bound_or_promote_trim(duration: float) -> None:
    from vid.schemas import VidError

    plan = (
        Plan(source="source.mp4").with_operation(Trim(start=0, end=2)).with_operation(AudioReplace(track="track.wav"))
    )
    with pytest.raises(VidError, match="known positive video duration"):
        compile_plan(plan, "out.mp4", durations={"source.mp4": duration})


@pytest.mark.parametrize("consumer", ["overlay", "overlay_keep", "retime", "mix", "running_silence", "transition"])
def test_cap_never_becomes_metadata_at_duration_consumers(consumer: str) -> None:
    from vid.plan import LayerAudio
    from vid.schemas import VidError

    operations = {
        "overlay": Overlay(source="layer.mp4"),
        "overlay_keep": Overlay(source="layer.mp4", audio=LayerAudio(policy="keep")),
        "retime": Retime(speed=2),
        "mix": AudioMix(track="track.wav"),
        "running_silence": Stitch(sources=["layer.mp4"]),
        "transition": Stitch(sources=["layer.mp4"], transition="fade"),
    }
    plan = Plan(source="source.mp4").with_operation(Trim(start=0, end=2)).with_operation(operations[consumer])
    facts: dict[str, float] = {"layer.mp4": 3.0}
    kwargs = {"has_audio": False, "source_audio": {"layer.mp4": True}} if consumer == "running_silence" else {}
    with pytest.raises(VidError, match="duration"):
        compile_plan(plan, "out.mp4", durations=facts, **kwargs)
    assumptions: list[str] = []
    if consumer == "transition":
        with pytest.raises(VidError, match="running edit"):
            compile_plan(plan, "out.mp4", durations=facts, pure_compile=True)
    else:
        compile_plan(plan, "out.mp4", durations=facts, pure_compile=True, assumptions=assumptions, **kwargs)
        assert any("sufficient material" in item for item in assumptions)


def test_incoming_silence_requires_its_own_duration() -> None:
    from vid.schemas import VidError

    plan = Plan(source="source.mp4").with_operation(Stitch(sources=["silent.mp4"]))
    with pytest.raises(VidError, match="matching-length silence"):
        compile_plan(
            plan, "out.mp4", durations={"source.mp4": 3}, source_audio={"silent.mp4": False}, pure_compile=True
        )


def test_complete_cut_is_empty_not_an_unknown_positive_length() -> None:
    from vid.plan import Cut
    from vid.schemas import VidError

    plan = Plan(source="source.mp4").with_operation(Cut(start=0, end=8)).with_operation(AudioReplace(track="track.wav"))
    with pytest.raises(VidError, match="empty"):
        compile_plan(plan, "out.mp4", durations={"source.mp4": 3})


def test_overlay_each_duration_consumer_discloses_cap() -> None:
    from vid.plan import LayerAudio

    plan = (
        Plan(source="source.mp4")
        .with_operation(Trim(start=0, end=2))
        .with_operation(Overlay(source="layer.mp4", audio=LayerAudio(policy="keep")))
    )
    assumptions: list[str] = []
    compile_plan(plan, "out.mp4", pure_compile=True, assumptions=assumptions)
    for purpose in ("layer length bound", "audio length bound", "base audio length bound"):
        assert any("sufficient material" in item and f"({purpose})" in item for item in assumptions), assumptions


def test_stitch_partial_known_never_promotes_cap() -> None:
    from vid.schemas import VidError

    plan = (
        Plan(source="unknown.mp4")
        .with_operation(Trim(start=0, end=2))
        .with_operation(Stitch(sources=["known.mp4"]))
        .with_operation(AudioReplace(track="track.wav"))
    )
    with pytest.raises(VidError, match="known positive video duration"):
        compile_plan(plan, "out.mp4", durations={"known.mp4": 3})
    assumptions: list[str] = []
    argv = compile_plan(plan, "out.mp4", durations={"known.mp4": 3}, pure_compile=True, assumptions=assumptions)
    assert "atrim=end=5.000000" in _graph(argv)
    assert any("sufficient material" in item for item in assumptions)


def test_cap_algebra_open_trim_and_cut_does_not_promote() -> None:
    from vid.compile import Compiler
    from vid.plan import Cut

    compiler = Compiler(Plan(source="unknown.mp4"), pure_compile=True)
    compiler.trim(Trim(start=0, end=8))
    compiler.trim(Trim(start=1))
    assert compiler.elapsed_cap == 7
    compiler.cut(Cut(start=1, end=8))
    assert compiler.elapsed is None
    assert compiler.elapsed_cap == 1
    assert compiler.assumptions == []


def test_ramp_endpoint_does_not_expand_prefix_or_tail() -> None:
    from vid.compile import Compiler

    compiler = Compiler(Plan(source="known.mp4"), durations={"known.mp4": 3}, has_audio=False, frame_rate=30)
    compiler.retime(Retime(ramp=[RampPoint(at=1, speed=2), RampPoint(at=2, speed=2)]))
    assert compiler.elapsed == 0.5
    assert compiler.elapsed_cap == 0.5


@pytest.mark.parametrize("pure", [False, True])
def test_transition_incoming_missing_refuses_at_transition_even_without_consumer(pure: bool) -> None:
    from vid.schemas import VidError

    plan = (
        Plan(source="known.mp4")
        .with_operation(AudioRemove())
        .with_operation(Stitch(sources=["unknown.mp4"], transition="fade", transition_duration=0.5))
    )
    with pytest.raises(VidError, match=r"incoming clip.*known positive duration"):
        compile_plan(plan, "out.mp4", durations={"known.mp4": 3}, pure_compile=pure)


def test_audio_mix_each_duration_consumer_discloses_cap() -> None:
    plan = Plan(source="source.mp4").with_operation(Trim(start=0, end=2)).with_operation(AudioMix(track="track.wav"))
    assumptions: list[str] = []
    compile_plan(plan, "out.mp4", pure_compile=True, assumptions=assumptions)
    for purpose in ("track pad and trim to the picture", "base audio length bound"):
        assert any("sufficient material" in item and f"({purpose})" in item for item in assumptions)


def test_secondary_audio_facts_survive_fallback_eligibility(tmp_path: Path) -> None:
    source = str(tmp_path / "missing.mp4")
    incoming = str(ensure_clips()["alpha"].path.resolve())
    printed = _show(Plan(source=source).with_operation(Stitch(sources=[incoming])), tmp_path / "out.mp4")
    assert printed.returncode == 0, printed.stderr
    assert f"source input 0 ({source!r}) has an audio stream" in printed.stderr
    assert f"stitch input 1 ({incoming!r}) has an audio stream" not in printed.stderr


def test_ramp_picture_cap_assumption_is_at_consumer() -> None:
    from vid.compile import Compiler

    assumptions: list[str] = []
    compiler = Compiler(Plan(source="unknown.mp4"), pure_compile=True, has_audio=False, assumptions=assumptions)
    compiler.retime(Retime(ramp=[RampPoint(at=0, speed=2), RampPoint(at=3, speed=2)]))
    assert compiler.elapsed is None
    assert compiler.elapsed_cap == 1.5
    assert assumptions == [
        "retime: sufficient material exists for the 1.5s nominal edit (completed ramp picture length bound)"
    ]


def test_trim_cap_intersection_without_promotion() -> None:
    from vid.compile import Compiler

    compiler = Compiler(Plan(source="unknown.mp4"), pure_compile=True)
    compiler.trim(Trim(start=0, end=2))
    compiler.trim(Trim(start=0, end=8))
    assert compiler.elapsed is None
    assert compiler.elapsed_cap == 2


def test_stitch_unknown_loses_cap() -> None:
    from vid.compile import Compiler

    compiler = Compiler(Plan(source="known.mp4"), durations={"known.mp4": 3}, pure_compile=True)
    compiler.stitch(Stitch(sources=["unknown.mp4"]))
    assert compiler.elapsed is None
    assert compiler.elapsed_cap is None


def test_repeated_transition_timing_is_actual_overlap() -> None:
    from vid.compile import Compiler

    compiler = Compiler(Plan(source="known.mp4"), durations={"known.mp4": 3})
    compiler.stitch(Stitch(sources=["known.mp4", "known.mp4"], transition="fade", transition_duration=0.5))
    assert compiler.elapsed == 8
    assert compiler.elapsed_cap == 8
    assert "offset=2.5[" in ";".join(compiler.filters)
    assert "offset=5.0[" in ";".join(compiler.filters)


@pytest.mark.parametrize("start", [0, 1, 8])
@pytest.mark.parametrize("operation", [AudioMix, AudioReplace])
def test_known_audio_placement_renders_picture_length(start: float, operation, tmp_path: Path) -> None:
    clips = ensure_clips()
    source, track = (str(clips[name].path.resolve()) for name in ("alpha", "bravo"))
    plan = Plan(source=source).with_operation(operation(track=track, start=start))
    output = tmp_path / "placed.mp4"
    printed = _show(plan, output)
    assert printed.returncode == 0, printed.stderr
    executed = subprocess.run(shlex.split(printed.stdout), capture_output=True, text=True, timeout=60)
    assert executed.returncode == 0, executed.stderr
    measured = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert measured.returncode == 0, measured.stderr
    streams = {s["codec_type"]: s for s in json.loads(measured.stdout)["streams"]}
    assert float(streams["video"]["duration"]) == 3
    assert float(streams["audio"]["duration"]) == pytest.approx(3, abs=1024 / int(streams["audio"]["sample_rate"]))


@pytest.mark.parametrize("verb", ["replace", "mix"])
def test_known_silent_track_refuses_before_ffmpeg(verb: str, tmp_path: Path) -> None:
    """A track PROBED as video-only is refused by name, by print and by render.

    Before: `--print-command` exited 0 with an empty stderr and handed out a
    `[1:a]` that ffmpeg rejected with exit 234 ("matches no streams") on 6.1.1
    and nightly alike. Unknown presence is a different state and still prints
    with its assumption disclosed (`test_secondary_unknown_audio_retains_tristate`).
    """
    source = str(ensure_clips()["alpha"].path.resolve())
    track = tmp_path / "video-only.mp4"
    run = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x240:r=30:d=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(track),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stderr
    operation = AudioReplace(track=str(track)) if verb == "replace" else AudioMix(track=str(track))
    plan = Plan(source=source).with_operation(operation)
    named = f"Cannot take audio from {str(track)!r}: it carries no audio stream."
    printed = _show(plan, tmp_path / "printed.mp4")
    assert printed.returncode != 0, f"[refusal-message] printed a command for a silent track: {printed.stdout}"
    assert printed.stdout == "", printed.stdout
    assert named in printed.stderr, f"[refusal-message] {printed.stderr}"
    assert "Supply a" in printed.stderr, f"[refusal-message] no remedy: {printed.stderr}"
    output = tmp_path / "rendered.mp4"
    rendered = subprocess.run(
        [VID, "render", str(output)], input=plan.model_dump_json(), capture_output=True, text=True, timeout=60, cwd=REPO
    )
    assert rendered.returncode != 0, "[refusal-message] rendered a silent track"
    assert named in rendered.stderr, f"[refusal-message] {rendered.stderr}"
    assert "matches no streams" not in rendered.stderr, f"[refusal-message] reached ffmpeg: {rendered.stderr}"
    assert not output.exists(), "[refusal-message] output written"
    unknown = Plan(source=source).with_operation(AudioReplace(track=str(tmp_path / "absent.wav")))
    shown = _show(unknown, tmp_path / "unknown.mp4")
    assert shown.returncode == 0, shown.stderr
    assert f"audio track input 1 ({str(tmp_path / 'absent.wav')!r}) has an audio stream" in shown.stderr


@pytest.mark.parametrize("verb", ["replace", "mix"])
def test_compile_boundary_known_silent_track_refuses_unknown_assumes(verb: str) -> None:
    """The compiler boundary itself, with the presence facts `lib.render` supplies."""
    from vid.schemas import VidError

    source = str(ensure_clips()["alpha"].path.resolve())
    track = "/media/track.mp4"
    operation = AudioReplace(track=track) if verb == "replace" else AudioMix(track=track)
    plan = Plan(source=source).with_operation(operation)
    with pytest.raises(VidError, match=r"Cannot take audio from '/media/track\.mp4': it carries no audio stream\."):
        compile_plan(plan, "out.mp4", durations={source: 3.0}, source_audio={track: False})
    assumptions: list[str] = []
    argv = compile_plan(plan, "out.mp4", source_audio={track: None}, assumptions=assumptions, pure_compile=True)
    assert "[1:a]" in _graph(argv)
    assert "audio track input 1 ('/media/track.mp4') has an audio stream" in assumptions


def test_repeated_identical_omission_is_counted(tmp_path: Path, ffmpeg_path: str) -> None:
    """Two fallback stitches skip the SAME step twice; the note says so once, with a count."""
    clips = ensure_clips()
    alpha, bravo, charlie = (str(clips[name].path.resolve()) for name in ("alpha", "bravo", "charlie"))
    plan = Plan(source=alpha).with_operation(Stitch(sources=[bravo])).with_operation(Stitch(sources=[charlie]))
    printed = _show(plan, tmp_path / "out.mp4", ffmpeg_path)
    assert printed.returncode == 0, printed.stderr
    clause = "stitch: setsar (sample aspect normalization, needs clip sizes) x2"
    assert clause in printed.stderr, f"[note-count] {printed.stderr}"


def test_trim_past_nominal_cap_clamps_before_stitch() -> None:
    """A trim starting past the nominal cap leaves nothing; what follows starts from zero."""
    from vid.compile import Compiler

    compiler = Compiler(Plan(source="unknown.mp4"), durations={"known.mp4": 3}, has_audio=False, pure_compile=True)
    compiler.trim(Trim(start=0, end=2))
    compiler.trim(Trim(start=3))
    assert compiler.elapsed_cap == 0, f"[cap-clamp] emptied edit has nominal cap {compiler.elapsed_cap}"
    compiler.stitch(Stitch(sources=["known.mp4"]))
    assert compiler.elapsed_cap == 3, f"[cap-clamp] 3s stitched onto an empty edit gave {compiler.elapsed_cap}"


def test_transition_unknown_running_length_is_a_named_refusal(tmp_path: Path) -> None:
    """The refusal is the contract: an unnamed crash in its place is a defect, not a refusal."""
    from vid.schemas import VidError

    clips = ensure_clips()
    alpha, bravo = (str(clips[name].path.resolve()) for name in ("alpha", "bravo"))
    plan = Plan(source=alpha).with_operation(Stitch(sources=[bravo], transition="fade", transition_duration=0.5))
    caught: Exception | None = None
    try:
        compile_plan(plan, str(tmp_path / "x.mp4"), durations={bravo: probe_duration(bravo)})
    except Exception as error:
        caught = error
    if caught is None:
        pytest.fail("[refusal-message] compiled a transition onto an edit of unknown length")
    if not isinstance(caught, VidError):
        pytest.fail(f"[refusal-message] unnamed {type(caught).__name__} instead of a named refusal: {caught}")
    assert "running edit's known positive duration" in str(caught), f"[refusal-message] {caught}"


@pytest.mark.parametrize("duration", [None, 0.0, float("nan")])
def test_copy_compile_boundary_refuses_unmeasured_duration(duration: float | None) -> None:
    """Library callers reach `compile_plan` without the probe; copy still needs a measured length."""
    from vid.schemas import VidError

    source = str(ensure_clips()["alpha"].path.resolve())
    plan = Plan(source=source).with_operation(AudioRemove())
    durations = None if duration is None else {source: duration}
    with pytest.raises(VidError, match="Picture stream-copy needs a known positive video duration"):
        compile_plan(plan, "out.mp4", video_codec="copy", durations=durations)


def test_unknown_rate_ramp_with_underflowing_length_discloses_its_missing_bound() -> None:
    """Without a frame rate nothing rounds the ramp up to a frame, so a ramp whose length
    underflows to 0.0 has no edit length to bound the picture by: the skip must be named."""
    from vid.compile import Compiler

    compiler = Compiler(Plan(source="src.mp4"), has_audio=False, frame_rate=None, pure_compile=True)
    compiler.retime(Retime(ramp=[RampPoint(at=0, speed=2), RampPoint(at=5e-324, speed=2)]))
    assert compiler.elapsed_cap == 0.0
    clause = "retime: trim,setpts (completed ramp picture length bound, needs edit length)"
    assert clause in compiler.omissions, f"[note-ramp-bound] {compiler.omissions}"


def test_fallback_overlay_names_its_missing_layer_bound(tmp_path: Path, ffmpeg_path: str) -> None:
    """The overlay's own skipped step, by name -- not only the generic fallback postamble."""
    clips = ensure_clips()
    alpha, bravo = (str(clips[name].path.resolve()) for name in ("alpha", "bravo"))
    plan = Plan(source=alpha).with_operation(Overlay(source=bravo))
    printed = _show(plan, tmp_path / "out.mp4", ffmpeg_path)
    assert printed.returncode == 0, printed.stderr
    clause = "overlay: trim,setpts (layer length bound, needs edit length)"
    assert clause in printed.stderr, f"[note-overlay] {printed.stderr}"
