"""Top level entry point for the Vid library."""

from pathlib import Path

from vid.core import manifest
from vid.core import skill as skill_module
from vid.schemas import Manifest


def load_manifest() -> Manifest:
    """The tool's manifest as structured data, read from the SMART_TOOL.md shipped inside the package."""
    return manifest.load_manifest()


def skill() -> str:
    """The tool's skill: the manifest body and the capability list, wrapped so a reader knows where its files are."""
    return skill_module.skill()


def skill_directory() -> Path:
    """The installed package root, where the files the skill names can be read."""
    return skill_module.skill_directory()


def skill_resources() -> list[str]:
    """The files the skill lists, as paths relative to the skill directory. Every one ships inside the package."""
    return skill_module.skill_resources()


def repository_url() -> str | None:
    """The tool's canonical source, from the package metadata, or None when the package declares none."""
    return skill_module.repository_url()


def render(plan, output: str, *, print_command: bool = False) -> str:
    """Compile a plan and run it, or show what would run.

    Probing happens here rather than in the compiler: durations are a property of
    the files, not of the plan, and keeping the compiler pure is what lets every
    other verb work with no ffmpeg installed.
    """
    import subprocess

    from vid.compile import compile_plan
    from vid.plan import Stitch
    from vid.probe import duration, have_ffmpeg
    from vid.schemas import VidError

    needs_durations = any(isinstance(op, Stitch) and op.transition for op in plan.operations)
    durations: dict[str, float] = {}
    if needs_durations:
        paths = [plan.source] + [s for op in plan.operations if isinstance(op, Stitch) for s in op.sources]
        durations = {path: duration(path) for path in dict.fromkeys(p for p in paths if p and p != "-")}

    command = compile_plan(plan, output, durations=durations)

    if print_command:
        import shlex

        return " ".join(shlex.quote(part) for part in command)

    if not have_ffmpeg():
        raise VidError(
            "ffmpeg is not on PATH, and rendering is the one thing that needs it. "
            "Run `vid check` for the install command for your system, or re-run with "
            "--print-command to see the invocation without running it."
        )
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-12:])
        raise VidError(f"ffmpeg failed:\n{tail}")
    return output


def check() -> str:
    """A report on this machine: which tier is reachable, and what would unlock the next."""
    import importlib.util
    import shutil
    import subprocess

    lines = ["vid -- what this installation can do", ""]
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")

    if ffmpeg:
        lines += [
            f"  [ok]      ffmpeg          {ffmpeg}",
            "            every mechanical verb works, and render can run.",
        ]
    else:
        lines += [
            "  [missing] ffmpeg",
            "            Plans still build without it -- only `render` needs it.",
            "            macOS:   brew install ffmpeg",
            "            Linux:   apt install ffmpeg   (or your distribution's package)",
            "            Windows: winget install Gyan.FFmpeg",
        ]
    if ffmpeg and not ffprobe:
        lines.append("  [warn]    ffprobe missing -- transitions cannot be placed without it.")

    speech = importlib.util.find_spec("faster_whisper") is not None
    lines.append("")
    if speech:
        lines.append("  [ok]      speech backend  faster-whisper")
    else:
        lines += [
            "  [missing] speech backend",
            "            Unlocks: index speech, and find-by-what-was-said.",
            "            uv tool install --force 'vid[speech] @ git+https://github.com/colombod/amplifier-smart-tools-video'",
        ]

    lines.append("")
    from vid.voice import available as voice_available

    if voice_available():
        lines.append("  [ok]      speech synth    piper")
    else:
        lines += [
            "  [missing] speech synth",
            "            Unlocks: narrate -- write a narration and fit it to the video.",
            "            uv tool install --force 'vid[voice] @ "
            "git+https://github.com/colombod/amplifier-smart-tools-video'",
        ]

    # THE PROVIDER SECTION, which was missing and produced a dead pointer.
    # The tier-2 refusal tells a caller "configure a provider -- `vid check`
    # says how", and until a DTU run put a stranger in front of it, `check`
    # inspected exactly two things and never mentioned a provider at all. The
    # instruction led somewhere that did not contain the answer.
    #
    # It could only be found on a machine with no credentials. Every box that
    # built this tool had a gh login, so the refusal never fired here and the
    # dead pointer was never followed.
    lines.append("")
    gh = shutil.which("gh")
    if gh:
        signed_in = subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0
        if signed_in:
            lines.append("  [ok]      provider        GitHub Copilot, via the gh CLI")
            lines.append("            Describing a transition or a moment in words works.")
        else:
            lines += [
                "  [missing] provider        gh is installed but not signed in",
                '            Unlocks: --transition "soft and dreamy", and find-by-meaning.',
                "            gh auth login",
            ]
    else:
        lines += [
            "  [missing] provider        none configured",
            "            Unlocks: describing a transition or a moment in words instead of naming it.",
            "            Everything else works without one -- naming a preset, and",
            "            searching for words the speaker actually used, need no provider.",
            "            Install the GitHub CLI and sign in:  https://cli.github.com/",
            "            then: gh auth login",
        ]

    lines += ["", "  Nothing here is sent anywhere. This is a report on your machine."]
    return "\n".join(lines)


def resolve_transition(
    text: str,
    first: str | None = None,
    second: str | None = None,
    duration: float = 0.5,
) -> tuple[str, str | None, str | None, str | None]:
    """A `--transition` value, resolved to something ffmpeg can actually run.

    Three tiers. A NAME resolves with no model at all. A DESCRIPTION gets a
    closed set of 58 to choose from, so a wrong answer is detectable rather than
    plausible. And when the model reports that nothing in the set fits, a new
    expression is WRITTEN -- then rendered as a probe and measured, because
    unbounded output is the one thing here that cannot be trusted on sight.

    Returns `(preset, requested, rationale, expression)`.
    """
    from vid.transitions import looks_like_a_description, resolve_with_clips

    intelligence = None
    if looks_like_a_description(text):
        try:
            from vid.intelligence.interface import default_intelligence

            intelligence = default_intelligence()
            intelligence.preflight()
        except Exception:
            # Left as None so the resolver can explain the situation properly --
            # it knows whether a model was needed, and this does not.
            intelligence = None
    return resolve_with_clips(text, first, second, duration, intelligence)


def verify(
    video: str,
    *,
    expect_duration: float | None = None,
    tolerance: float = 0.15,
    expect_resolution: str | None = None,
    expect_audio: bool = False,
    expect_transition_at: float | None = None,
    expect_no_black_frames: bool = False,
    longest_black: float = 0.5,
) -> tuple[bool, str]:
    """Check a rendered video against named properties. Returns `(passed, report)`.

    Deterministic throughout: every answer comes from ffprobe or frame arithmetic,
    so this is the one kind of verification that can honestly grade something a
    model produced.
    """
    from vid import verify as checks
    from vid.probe import have_ffmpeg
    from vid.schemas import VidError

    if not have_ffmpeg():
        raise VidError(
            "verify reads actual frames, so it needs ffmpeg on PATH. "
            "Run `vid check` for the install command for your system."
        )

    results = []
    if expect_duration is not None:
        results.append(checks.check_duration(video, expect_duration, tolerance))
    if expect_resolution is not None:
        results.append(checks.check_resolution(video, expect_resolution))
    if expect_audio:
        results.append(checks.check_audio(video))
    if expect_transition_at is not None:
        results.append(checks.check_transition_at(video, expect_transition_at))
    if expect_no_black_frames:
        results.append(checks.check_no_black_frames(video, longest_black))

    text, passed = checks.report(results)
    return passed, text


def index(
    video: str,
    *,
    speech: bool = True,
    model_size: str = "base",
    vision: bool = False,
    yes: bool = False,
) -> str:
    """Build (or extend) a video's index and report what it holds."""
    import json
    import sys

    from vid.index import build, describe, index_path
    from vid.schemas import VidError

    record = build(video, speech=speech, model_size=model_size)

    if vision:
        pending = [shot for shot in record.get("shots", []) if not shot.get("description")]
        if not pending:
            return _index_report(video, record, note="every shot already described")

        # COST IS STATED BEFORE IT IS SPENT. A verb that quietly makes a model
        # call per shot on a two-hour recording is how people stop trusting a
        # tool. A person gets asked; an agent gets the number in the refusal and
        # can decide for itself -- neither one is surprised by a bill.
        notice = (
            f"{video}: {len(pending)} shot(s) to describe.\n"
            "  Shot detection already reduced this from every frame in the video "
            "to one frame per shot."
        )
        if not yes:
            if sys.stdin.isatty():
                sys.stderr.write(notice + "\n")
                if input("  Describe them? [y/N] ").strip().lower() not in {"y", "yes"}:
                    return "Nothing described. The index is unchanged."
            else:
                raise VidError(notice + "\n  Refusing to spend that unasked. Re-run with --yes.")

        intelligence = None
        try:
            from vid.intelligence.interface import default_intelligence

            intelligence = default_intelligence()
            intelligence.preflight()
        except Exception:
            intelligence = None
        if intelligence is None:
            raise VidError(
                "Describing what is on screen needs a model, and none is configured. "
                "Shot detection and speech indexing keep working without one -- "
                "`vid check` says how to configure a provider."
            )

        record = describe(video, record, intelligence)
        index_path(video).write_text(json.dumps(record, indent=2), encoding="utf-8")

    return _index_report(video, record)


def _index_report(video: str, record: dict, note: str = "") -> str:
    from vid.index import index_path

    shots = record.get("shots", [])
    described = sum(1 for shot in shots if shot.get("description"))
    lines = [
        f"indexed {video}",
        f"  {record['duration']:.1f}s, fingerprint {record['fingerprint']}",
        f"  shots  {len(shots)}" + (f", {described} described" if described else ""),
        f"  speech {len(record.get('speech', []))} passages" if "speech" in record else "  speech not indexed",
        f"  stored {index_path(video)}",
    ]
    if note:
        lines.append(f"  ({note})")
    return "\n".join(lines)


def find(query: str, video: str, *, show: bool = False) -> None:
    """Locate a moment, and either describe it or emit a plan trimmed to it."""
    import sys

    from vid.find import find as search
    from vid.index import chunks_of, load
    from vid.plan import Plan, Trim, write_plan
    from vid.schemas import VidError

    record = load(video)
    if record is None:
        raise VidError(
            f"{video!r} has not been indexed yet. Run `vid index {video}` first -- "
            "it is the expensive step, and every later question reuses it."
        )

    intelligence = None
    try:
        from vid.intelligence.interface import default_intelligence

        intelligence = default_intelligence()
        intelligence.preflight()
    except Exception:
        intelligence = None

    from vid.find import visual_chunks

    hits = search(chunks_of(record), query, intelligence, seen=visual_chunks(record))
    if not hits:
        raise VidError(f"Nothing in {video!r} matches {query!r}.")

    if show:
        for hit in hits:
            sys.stdout.write(
                f"{hit.start:.2f}-{hit.end:.2f}s  [{hit.how}] {', '.join(hit.chunk_ids)}\n"
                f"    {hit.text}\n" + (f"    -- {hit.rationale}\n" if hit.rationale else "")
            )
        return

    best = hits[0]
    write_plan(Plan(source=video).with_operation(Trim(start=best.start, end=best.end)))


def audio_extract(video: str, output: str) -> str:
    """Pull a video's audio out to a file.

    Ends a chain rather than continuing one: it writes an audio file, not a plan,
    so there is nothing meaningful to pipe onward. Said plainly in `--help`
    because every other verb here does continue a chain, and a caller who expects
    that is owed the exception in writing.
    """
    import subprocess

    from vid.probe import have_ffmpeg
    from vid.schemas import VidError

    if not have_ffmpeg():
        raise VidError(
            "Extracting audio decodes the file, so it needs ffmpeg on PATH. "
            "Run `vid check` for the install command for your system."
        )

    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video, "-vn", output],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[-1] if detail else "ffmpeg gave no reason"
        raise VidError(f"Could not extract audio from {video!r}: {reason}")
    return f"wrote {output}"


def narrate(
    video: str,
    prompt: str,
    *,
    out: str | None = None,
    script_only: bool = False,
    voice: str | None = None,
    mix: bool | None = None,
) -> str:
    """Write a narration for a video, fit it to the timing, and lay it on.

    Returns a human-readable report. The script is always printed before any
    audio is synthesised, because narration is the most expensive thing here to
    get wrong and every other artefact in this tool is inspectable before it is
    committed to.
    """
    import tempfile
    from pathlib import Path

    from vid.index import load
    from vid.narrate import assemble, fit, write_script
    from vid.schemas import VidError
    from vid.voice import DEFAULT_VOICE, Speaker

    record = load(video)
    if record is None:
        raise VidError(
            f"{video!r} has not been indexed yet, and narration is written against what is "
            f"actually in the video. Run `vid index {video}` first."
        )

    intelligence = None
    try:
        from vid.intelligence.interface import default_intelligence

        intelligence = default_intelligence()
        intelligence.preflight()
    except Exception:
        intelligence = None
    if intelligence is None:
        raise VidError(
            "Writing a narration needs a model, and none is configured. "
            "`vid check` says how."
        )

    script = write_script(record, prompt, intelligence)
    if script_only:
        return script.to_json()

    speaker = Speaker(voice or DEFAULT_VOICE)
    workdir = Path(tempfile.mkdtemp(prefix="vid-narrate-"))
    fit(script, speaker, workdir, intelligence)

    total = record.get("duration", 0.0)
    track = assemble(script, workdir / "narration.wav", total)

    report = [f"narration for {video}", ""]
    report += [line.report() for line in script.lines]
    unfitted = script.unfitted()
    if unfitted:
        report += ["", f"  {len(unfitted)} line(s) did not fit:"]
        report += [f"    {line.start:.2f}s -- {line.note}" for line in unfitted]

    if out is None:
        report += ["", f"  narration track: {track}",
                   "  Lay it on with:  vid audio " +
                   ("mix" if mix else "replace") + f" {video} --with {track} | vid render out.mp4"]
        return "\n".join(report)

    # Lay it on through the existing audio verbs rather than a parallel path --
    # one way to put sound on a video, not two.
    from vid.compile import compile_plan
    from vid.plan import AudioMix, AudioReplace, Plan

    has_speech = bool(record.get("speech"))
    layer = mix if mix is not None else has_speech
    operation = AudioMix(track=str(track), level=-6.0) if layer else AudioReplace(track=str(track))
    command = compile_plan(Plan(source=video).with_operation(operation), out, durations={video: total})

    import subprocess

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise VidError(f"Could not render {out!r}: {detail[-1] if detail else '?'}")

    report += ["", f"  wrote {out} ({'mixed over' if layer else 'replacing'} the original audio)"]
    return "\n".join(report)
