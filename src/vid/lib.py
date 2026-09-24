"""Top level entry point for the Vid library.

EVERYTHING THE CLI CAN DO IS REACHABLE HERE. Every plan-editing capability --
trim, cut, retime, zoom, stitch, caption, the audio edits, recolor, vignette,
grade, lut, plan display -- owns its own validation, normalization and
operation construction as a public function that takes and returns a `Plan`.
The CLI's job is only argument parsing, reading the piped plan off stdin, and
writing the result back out; a Python caller does the same work by building or
receiving a `Plan` directly and calling these functions with no CLI involved.
"""

import math
from pathlib import Path
from typing import Literal

from vid.core import manifest
from vid.core import skill as skill_module
from vid.plan import Plan
from vid.schemas import DEFAULT_INTELLIGENCE_MODEL, Manifest, ReasoningEffort, VidError


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


def render(plan: Plan, output: str, *, print_command: bool = False, video_codec: str = "libx264") -> str:
    """Compile a plan and run it, or show what would run.

    Probing happens here rather than in the compiler: durations are a property of
    the files, not of the plan, and keeping the compiler pure is what lets every
    other verb work with no ffmpeg installed.
    """
    import subprocess

    from vid.compile import compile_plan, validate_video_codec
    from vid.plan import AudioMix, AudioReplace, Overlay, Stitch, Zoom
    from vid.plan import Retime as _Retime
    from vid.probe import dimensions, frame_rate, has_audio, have_ffmpeg, video_duration

    validate_video_codec(plan, output, video_codec)

    # Retime needs the source duration too: without it the audio cannot be
    # bounded to the length the edit means, and atempo's rounding decides the
    # container's duration instead. `audio replace`/`audio mix` need it for the
    # same reason -- both pad-then-trim the incoming track to "however long the
    # video is right now", and without a known length that bound silently
    # becomes "no bound at all", padding the incoming track's silence forever
    # rather than to the video's actual length.
    needs_durations = any(
        (isinstance(op, Stitch) and op.transition) or isinstance(op, (_Retime, AudioReplace, AudioMix))
        for op in plan.operations
    )
    durations: dict[str, float] = {}
    if needs_durations:
        paths = [plan.source] + [s for op in plan.operations if isinstance(op, Stitch) for s in op.sources]
        durations = {path: video_duration(path) for path in dict.fromkeys(p for p in paths if p and p != "-")}
    if video_codec == "copy" and plan.source:
        from vid.probe import copy_video_duration

        durations[plan.source] = copy_video_duration(plan.source)

    # Probed here beside the durations, and for the same reason: whether a file
    # has sound is a property of the FILE, not of the plan, and keeping that out
    # of the compiler is what lets every other verb run with no ffmpeg at all.
    source_has_audio = has_audio(plan.source) if plan.source else True

    # The same question, asked of every clip a stitch pulls in. It used to be
    # asked only of `plan.source`, so stitching a silent clip emitted a stream
    # specifier for a stream that does not exist and ffmpeg refused the command
    # with a message naming neither the file nor the reason.
    stitch_sources = [s for op in plan.operations if isinstance(op, Stitch) for s in op.sources if s and s != "-"]
    source_audio = {path: has_audio(path) for path in dict.fromkeys(stitch_sources)}

    # Sizes, for the same reason and on the same terms: `concat` needs matching
    # resolution and SAR, and which size a file is cannot be read from the plan.
    # The plan's own source is included because it IS the target every stitched
    # clip is resolved to.
    # Overlay layers are sized for the same reason: an image or video matte
    # has to be scaled to the layer it cuts, and that size is a fact about
    # the file rather than anything the plan can state.
    overlay_sources = [op.source for op in plan.operations if isinstance(op, Overlay) and op.source]
    source_sizes: dict[str, tuple[int, int]] = {}
    if stitch_sources or overlay_sources:
        sized = [*stitch_sources, *overlay_sources]
        sized = [plan.source, *sized] if plan.source else sized
        source_sizes = {path: size for path in dict.fromkeys(sized) if (size := dimensions(path)) is not None}
    from vid.plan import Retime

    # Retime needs the source's frame rate to put retimed frames back on a
    # uniform grid; zoom needs it too, as `zoompan`'s own per-frame clock
    # (see `Compiler.zoom`) -- and zoom additionally needs the source's own
    # dimensions, to pin `zoompan`'s output size rather than let it default
    # to `hd720` regardless of what was actually decoded.
    needs_frame_rate = any(isinstance(op, (Retime, Zoom)) for op in plan.operations)
    rate = frame_rate(plan.source) if plan.source and needs_frame_rate else None
    dims = dimensions(plan.source) if plan.source and any(isinstance(op, Zoom) for op in plan.operations) else None

    # RESOLVED BEFORE IT IS USED, not after. A caller who wrote `out.mp4` from
    # one working directory and reads the report from another cannot resolve a
    # bare relative path -- the same reason `index` and narration's default
    # track already report an absolute path.
    resolved_output = str(Path(output).resolve())
    command = compile_plan(
        plan,
        resolved_output,
        durations=durations,
        has_audio=source_has_audio,
        frame_rate=rate,
        dimensions=dims,
        source_audio=source_audio,
        source_sizes=source_sizes,
        video_codec=video_codec,
    )

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
        raise VidError(
            f"ffmpeg failed to render {resolved_output!r}:\n{tail}\n"
            "Check that every input file the plan names is readable, or run `vid check` "
            "to confirm this installation's ffmpeg and filters are working. `--print-command` "
            "shows the exact invocation without running it."
        )
    return resolved_output


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

    # Subtitle support is a BUILD feature, not a package. A user on macOS hit
    # this with ffmpeg installed and working: every verb ran, and `caption`
    # alone failed, because their build had no libass. Reporting "ffmpeg ok"
    # and nothing else was how they found out the hard way.
    if shutil.which("ffmpeg"):
        from vid.probe import has_subtitles_filter

        if has_subtitles_filter():
            lines.append("            subtitles filter present, so `caption` works.")
        else:
            lines += [
                "",
                "  [missing] ffmpeg subtitles",
                "            Your ffmpeg has no `subtitles` filter, so `caption` cannot burn",
                "            captions in. EVERY OTHER VERB IS FINE -- this is the only one.",
                "            It needs ffmpeg built with libass:",
                "            macOS:   brew install ffmpeg        (the full formula, not a slim tap)",
                "                     If brew leaves dependencies unconfigured, captions can",
                "                     still fail to find fonts. Reported from a real install:",
                "                       brew postinstall ca-certificates fontconfig gnutls glib openssl@3",
                "            Linux:   apt install ffmpeg         (Debian/Ubuntu builds include it)",
                "            Check:   ffmpeg -filters | grep subtitles",
            ]

    lines.append("")
    from vid.voice import available as voice_available

    if voice_available():
        lines.append("  [ok]      speech synth    piper")
    else:
        lines += [
            "  [missing] speech synth",
            "            Unlocks: narrate -- write a narration and fit it to the video.",
            (
                "            uv tool install --force 'vid[voice] @ "
                "git+https://github.com/colombod/amplifier-smart-tools-video'"
            ),
        ]

    # THE OPENAI SPEECH BACKEND, an ALTERNATIVE to piper for `narrate --voice
    # openai:<voice>`. Reported the same way as the provider section below:
    # whether the package is there, which profile would be used, which
    # environment variable it reads, and whether that variable is set --
    # never the key itself.
    lines.append("")
    if importlib.util.find_spec("openai") is not None:
        lines.append("  [ok]      openai package  installed (unlocks --voice openai:<voice>)")
    else:
        lines += [
            "  [missing] openai package",
            "            Unlocks: --voice openai:<voice> -- narrate through OpenAI's TTS",
            "            instead of piper. Used ONLY when named explicitly; piper being",
            "            unavailable never falls back to it.",
            (
                "            uv tool install --force 'vid[voice-openai] @ "
                "git+https://github.com/colombod/amplifier-smart-tools-video'"
            ),
        ]

    from vid.config import provider_profile
    from vid.speech.openai_tts import DEFAULT_API_KEY_ENV

    try:
        section = provider_profile("openai", "default", default_env_var=DEFAULT_API_KEY_ENV)
    except VidError as error:
        lines.append(f"  [warn]    openai config   {error}")
    else:
        import os

        env_var = section["api_key_env"]
        if os.environ.get(env_var):
            lines.append(f"  [ok]      openai key      {env_var} is set (profile 'default')")
        else:
            lines.append(f"  [missing] openai key      {env_var} is not set (profile 'default')")

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

    lines += [
        "",
        "  `vid check` itself sends nothing -- this is a report on your machine. Piper",
        "  narration runs entirely locally. `--voice openai:<voice>` is the one exception:",
        "  it sends the narration TEXT (never a key) to OpenAI over the network.",
    ]
    return "\n".join(lines)


def resolve_transition(
    text: str,
    first: str | None = None,
    second: str | None = None,
    duration: float = 0.5,
    model: str = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: ReasoningEffort = "low",
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
    return resolve_with_clips(
        text, first, second, duration, intelligence, model=model, reasoning_effort=reasoning_effort
    )


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
    pixel_threshold: float = 0.1,
    frame_threshold: float = 0.98,
) -> tuple[bool, str]:
    """Check a rendered video against named properties. Returns `(passed, report)`.

    Deterministic throughout: every answer comes from ffprobe or frame arithmetic,
    so this is the one kind of verification that can honestly grade something a
    model produced.
    """
    from vid import verify as checks
    from vid.probe import have_ffmpeg

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
        results.append(checks.check_no_black_frames(video, longest_black, pixel_threshold, frame_threshold))

    text, passed = checks.report(results)
    return passed, text


def index(
    video: str,
    *,
    speech: bool = True,
    model_size: str = "base",
    vision: bool = False,
    yes: bool = False,
    model: str = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: ReasoningEffort = "low",
) -> str:
    """Build (or extend) a video's index and report what it holds."""
    import json

    from vid.index import build, describe, index_path
    from vid.probe import have_ffmpeg, have_ffprobe

    # PREFLIGHT BEFORE ANY WORK STARTS. Shot detection shells out to ffmpeg and
    # the duration read shells out to ffprobe; both used to be discovered
    # missing only when one of those subprocess calls failed, well outside this
    # tool's own remedial error path. Checking first means the caller learns
    # what to install before any indexing work has spent time on the file.
    if not (have_ffmpeg() and have_ffprobe()):
        raise VidError(
            "Indexing reads the file directly -- shot detection needs ffmpeg and the duration "
            "read needs ffprobe, both on PATH before any indexing work starts. "
            "Run `vid check` for the install command for your system."
        )

    # THE PROVIDER IS CONFIRMED BEFORE `build()` EVER RUNS, not after. `build()`
    # writes the index to disk unconditionally -- on a video with no prior
    # index, a caller with no gh/Copilot access used to pay for the whole
    # build (shot detection, a real transcription) only to be refused
    # afterward, with a brand new index file left behind that nobody asked
    # for. Checking the provider first means a missing prerequisite leaves no
    # new state at all.
    intelligence = None
    if vision:
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
        if not _confirm_before_spending(notice, yes):
            return "Nothing described. The index is unchanged."

        record = describe(video, record, intelligence, model=model, reasoning_effort=reasoning_effort)
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


def _confirm_before_spending(notice: str, yes: bool) -> bool:
    """Whether to go ahead with a spend the caller has not pre-approved.

    THE PROMPT IS A DIAGNOSTIC, not a result, so it -- and the notice above it
    -- go to stderr. Only the caller's typed answer touches stdin. This used to
    go through the builtin `input()`, whose prompt argument is written to
    stdout regardless: an interactive `vid index --vision` interleaved that
    prompt into the machine-readable result `index` prints on completion.
    """
    import sys

    if yes:
        return True
    if not sys.stdin.isatty():
        raise VidError(notice + "\n  Refusing to spend that unasked. Re-run with --yes.")
    sys.stderr.write(notice + "\n")
    sys.stderr.write("  Describe them? [y/N] ")
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


def _no_match_message(video: str, query: str, *, spoke: bool, shown: bool, has_speech_index: bool) -> str:
    """Say which side of the index a described-tier miss actually covered.

    `vid.find.find` searches speech before vision, and hands the described
    (model) tier speech chunks in preference to vision chunks whenever both
    exist -- see its docstring. So a miss with a speech transcript but no
    vision descriptions means vision was never asked about at all, and the
    reverse means speech never was. Naming which one is missing gives a
    remedy that is actually true for this video's index, not a canned line.
    """
    base = f"Nothing in {video!r} matches {query!r}."
    if spoke and not shown:
        return (
            f"{base} The search covered what was SAID, not what was SHOWN -- "
            f"`vid index {video} --vision` describes the shots so it can be searched too."
        )
    if shown and not has_speech_index:
        return (
            f"{base} The search covered what was SHOWN, not what was SAID -- "
            f"`vid index {video}` adds a speech transcript so it can be searched too."
        )
    return f"{base} Try words closer to what was actually said or shown."


def find(
    query: str,
    video: str,
    *,
    show: bool = False,
    model: str = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: ReasoningEffort = "low",
) -> None:
    """Locate a moment, and either describe it or emit a plan trimmed to it."""
    import sys

    from vid.find import find as search
    from vid.index import chunks_of, load
    from vid.plan import Trim, write_plan

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

    speech_chunks = chunks_of(record)
    seen_chunks = visual_chunks(record)
    hits = search(speech_chunks, query, intelligence, seen=seen_chunks, model=model, reasoning_effort=reasoning_effort)
    if not hits:
        raise VidError(
            _no_match_message(
                video,
                query,
                spoke=bool(speech_chunks),
                shown=bool(seen_chunks),
                has_speech_index="speech" in record,
            )
        )

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

    if not have_ffmpeg():
        raise VidError(
            "Extracting audio decodes the file, so it needs ffmpeg on PATH. "
            "Run `vid check` for the install command for your system."
        )

    resolved_output = str(Path(output).resolve())
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video, "-vn", resolved_output],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[-1] if detail else "ffmpeg gave no reason"
        raise VidError(
            f"Could not extract audio from {video!r}: {reason}\n"
            "Check the file is a readable video, or run `vid check` to confirm ffmpeg is working."
        )
    return f"wrote {resolved_output}"


def audio_remove(plan: Plan) -> Plan:
    """Drop the audio track. The result is a silent video."""
    from vid.plan import AudioRemove

    return plan.with_operation(AudioRemove())


def audio_replace(plan: Plan, track: str, start: float = 0.0) -> Plan:
    """Swap the audio track for another file's. Result is always the video's length."""
    from vid.plan import AudioReplace

    _validate_audio_start(start)
    return plan.with_operation(AudioReplace(track=track, start=start))


def audio_mix(plan: Plan, track: str, level: float = -18.0, start: float = 0.0) -> Plan:
    """Lay another track under the existing audio, keeping both."""
    from vid.plan import AudioMix

    _validate_audio_start(start)
    return plan.with_operation(AudioMix(track=track, level=level, start=start))


def _validate_audio_start(start: float) -> None:
    if not math.isfinite(start) or start < 0:
        raise VidError("Audio start must be finite, nonnegative seconds.")


def narrate(
    video: str,
    prompt: str,
    *,
    out: str | None = None,
    script_only: bool = False,
    voice: str | None = None,
    mix: bool | None = None,
    model: str = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: ReasoningEffort = "low",
) -> str:
    """Write a narration for a video, fit it to the timing, and lay it on.

    Returns a human-readable report. The script is always printed before any
    audio is synthesised, because narration is the most expensive thing here to
    get wrong and every other artefact in this tool is inspectable before it is
    committed to.
    """
    from pathlib import Path
    import shutil
    import subprocess
    import tempfile

    from vid.core.manifest import manifest_install
    from vid.index import fingerprint, load
    from vid.narrate import assemble, fit, write_script
    from vid.probe import have_ffmpeg
    from vid.speech.interface import resolve_speech, speech_preflight

    record = load(video)
    if record is None:
        raise VidError(
            f"{video!r} has not been indexed yet, and narration is written against what is "
            f"actually in the video. Run `vid index {video}` first."
        )

    # CHECK FIRST, WORK SECOND. Synthesis needs a speech backend, and this
    # used to be discovered only after `write_script` had already spent a
    # model call -- the missing prerequisite was found on the far side of a
    # bill. `--script-only` never touches a speech backend at all, so it is
    # exempt from this preflight.
    #
    # ffmpeg is checked here too, for the same reason: `assemble` (below) lays
    # the synthesised lines onto a silent bed with ffmpeg regardless of
    # `--out`, so finding a missing binary only after script writing AND
    # synthesis had already run would be the exact bill this preflight exists
    # to avoid paying.
    #
    # ffmpeg is checked BEFORE speech, so that missing binary is reported
    # before the model write happens, regardless of which backend is chosen.
    if not script_only:
        if not have_ffmpeg():
            raise VidError(
                "Assembling a narration lays every synthesised line onto a silent bed with "
                f"ffmpeg, so it needs ffmpeg on PATH first. Install it ({manifest_install('ffmpeg')}) "
                "-- see `vid check` for the command for your system."
            )
        speech_preflight(voice)

    intelligence = None
    try:
        from vid.intelligence.interface import default_intelligence

        intelligence = default_intelligence()
        intelligence.preflight()
    except Exception:
        intelligence = None
    if intelligence is None:
        raise VidError("Writing a narration needs a model, and none is configured. `vid check` says how.")

    script = write_script(record, prompt, intelligence, model=model, reasoning_effort=reasoning_effort)
    if script_only:
        return script.to_json()

    speaker = resolve_speech(voice)
    total = record.get("duration", 0.0)

    # SCOPED, LIKE VISION'S FRAME EXTRACTION. Every intermediate WAV -- one per
    # line, plus the assembled track -- lives in a directory that is deleted
    # the moment this block ends, so nothing here leaks the way an unscoped
    # `tempfile.mkdtemp` would.
    with tempfile.TemporaryDirectory(prefix="vid-narrate-") as work:
        workdir = Path(work)
        fit(script, speaker, workdir, intelligence, model=model, reasoning_effort=reasoning_effort)
        temp_track = assemble(script, workdir / "narration.wav", total)

        report = [f"narration for {video}", ""]
        report += [line.report() for line in script.lines]
        unfitted = script.unfitted()
        if unfitted:
            report += ["", f"  {len(unfitted)} line(s) did not fit:"]
            report += [f"    {line.start:.2f}s -- {line.note}" for line in unfitted]

        if out is None:
            # The scoped directory above is destroyed as soon as this block
            # ends, so the artefact reported back to the caller has to already
            # be somewhere durable -- a path into a directory that no longer
            # exists is not a usable track.
            durable_dir = _narration_dir()
            durable_dir.mkdir(parents=True, exist_ok=True)
            track = durable_dir / f"{fingerprint(video)}.wav"
            shutil.copyfile(temp_track, track)
            report += [
                "",
                f"  narration track: {track}",
                "  Lay it on with:  vid audio "
                + ("mix" if mix else "replace")
                + f" {video} --with {track} | vid render out.mp4",
            ]
            return "\n".join(report)

        # Lay it on through the existing audio verbs rather than a parallel path --
        # one way to put sound on a video, not two.
        from vid.compile import compile_plan
        from vid.plan import AudioMix, AudioReplace, Plan

        # RESOLVED BEFORE IT IS WRITTEN, same as `render` and `audio_extract` --
        # a relative `--out` has to come back as something the caller can find
        # regardless of which directory reads the report.
        resolved_out = str(Path(out).resolve())
        has_speech = bool(record.get("speech"))
        layer = mix if mix is not None else has_speech
        operation = AudioMix(track=str(temp_track), level=-6.0) if layer else AudioReplace(track=str(temp_track))
        command = compile_plan(Plan(source=video).with_operation(operation), resolved_out, durations={video: total})

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            raise VidError(
                f"Could not render {resolved_out!r}: {detail[-1] if detail else '?'}\n"
                "Check the narration track and source video are both readable, or run "
                "`vid check` to confirm ffmpeg is working."
            )

        report += ["", f"  wrote {resolved_out} ({'mixed over' if layer else 'replacing'} the original audio)"]
        return "\n".join(report)


def _narration_dir() -> Path:
    """Where a narration track persists once written.

    The synthesis workdir is a scoped temp directory, gone the moment
    `narrate` returns, so the track this reports back to the caller (when
    `--out` is omitted) has to be copied somewhere durable first. Same
    convention as the index and the voice cache: an env override, else the
    XDG state home -- a caller's next `vid audio` command can still find it.
    """
    import os

    override = os.environ.get("VID_NARRATION_DIR")
    if override:
        # RESOLVED, same as `render`'s and `audio_extract`'s output paths: a
        # relative override reported back to a caller reading from a
        # different cwd would name a location only this process could find.
        return Path(override).resolve()
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "vid" / "narration"


def recolor_op(video: str, reference: str, strength: float = 1.0):
    """Measure both palettes and build the operation that maps one onto the other.

    Everything here is arithmetic: frames sampled with ffmpeg, statistics
    computed in Python, no provider, no network, nothing uploaded.
    """
    from pathlib import Path

    from vid.color import measure_image, measure_video
    from vid.plan import Recolor

    if not Path(reference).is_file():
        raise VidError(
            f"No such reference image: {reference!r}. Check the path is correct and relative to the current directory."
        )

    source = measure_video(video)
    target = measure_image(reference)
    return Recolor(
        reference=reference,
        source_mean=source.mean,
        source_std=source.std,
        reference_mean=target.mean,
        reference_std=target.std,
        strength=strength,
    )


def recolor(plan: Plan, like: str, strength: float = 1.0) -> Plan:
    """Map a plan's own source video's colour onto a reference image's."""
    if plan.source is None:
        raise VidError("recolor needs a plan with a source video to measure.")
    operation = recolor_op(plan.source, like, strength)
    return plan.with_operation(operation)


# region: plan-editing capabilities
#
# Every one of these takes the plan being edited and returns the next one; none
# of them touch stdin, stdout, or argv. The CLI's job is only to read the
# incoming plan (or start one from a source path) and write the one returned
# here back out -- everything a caller sees a verb "decide" happens in this
# region, and is exactly as reachable from Python as from the command line.


def trim(plan: Plan, start: str = "0", end: str | None = None) -> Plan:
    """Keep a time range, discard the rest. `start`/`end` accept `90`, `1:30`, `0:10.5`."""
    from vid.plan import Trim
    from vid.timecode import parse_timecode

    return plan.with_operation(Trim(start=parse_timecode(start), end=parse_timecode(end) if end else None))


def cut(plan: Plan, start: str, end: str) -> Plan:
    """Remove a time range, keeping what surrounds it."""
    from vid.plan import Cut
    from vid.timecode import parse_timecode

    return plan.with_operation(Cut(start=parse_timecode(start), end=parse_timecode(end)))


def retime(plan: Plan, speed: str | None = None, ramp: str | None = None) -> Plan:
    """Change speed, constantly (`speed`, e.g. `2x`) or along a curve (`ramp`, e.g.
    `"1x@0 0.25x@1:05 1x@1:12"`). Exactly one of the two must be given."""
    from vid.plan import RampPoint, Retime
    from vid.timecode import parse_speed, parse_timecode

    if (speed is None) == (ramp is None):
        raise VidError("Give exactly one of --speed (a constant) or --ramp (a curve).")
    if speed is not None:
        return plan.with_operation(Retime(speed=parse_speed(speed)))
    assert ramp is not None  # guaranteed by the mutual-exclusion check above

    points = []
    for token in ramp.split():
        value, _, at = token.partition("@")
        if not at:
            raise VidError(f"{token!r} is not a ramp point. Write `speed@time`, as in `0.25x@1:05`.")
        points.append(RampPoint(at=parse_timecode(at), speed=parse_speed(value)))
    return plan.with_operation(Retime(ramp=points))


def zoom(plan: Plan, to: float = 1.3, at: str | None = None, duration: float = 3.0) -> Plan:
    """Animated zoom (Ken Burns), optionally centred on a moment."""
    from vid.plan import Zoom
    from vid.timecode import parse_timecode

    return plan.with_operation(Zoom(to=to, at=parse_timecode(at) if at else None, duration=duration))


def stitch(
    plan: Plan | None,
    sources: list[str],
    *,
    transition: str | None = None,
    duration: float = 0.5,
    fit: Literal["fit", "fill"] | None = None,
    model: str = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: ReasoningEffort = "low",
) -> Plan:
    """Join clips onto `plan`, with or without a transition.

    Pass an existing `Plan` (continuing a pipe, or one built by another call
    here) and every clip to append in `sources`. Pass `None` and `sources`
    starts a fresh plan from its own first entry, appending the rest.
    """
    from vid.plan import Stitch

    if fit is not None and fit not in ("fit", "fill"):
        raise VidError(
            f"Unknown fit mode {fit!r}. Use `fit` to preserve aspect and pad the remainder "
            "with bars, or `fill` to preserve aspect and crop the overflow centred."
        )

    rest = list(sources)
    if plan is None:
        if not rest:
            raise VidError("stitch needs at least one clip to join on. Name another file.")
        plan = Plan(source=rest[0])
        rest = rest[1:]
    if not rest:
        raise VidError("stitch needs at least one clip to join on. Name another file.")

    preset, requested, rationale, expression = (None, None, None, None)
    if transition is not None:
        # The clips are handed over so a GENERATED transition can be proven
        # against the pair it will actually join. Tier 1 and tier 2 ignore them.
        first = plan.source if plan.source else (rest[0] if rest else None)
        second = rest[0] if rest else None
        preset, requested, rationale, expression = resolve_transition(
            transition, first, second, duration, model=model, reasoning_effort=reasoning_effort
        )

    return plan.with_operation(
        Stitch(
            sources=rest,
            fit=fit,
            transition=preset,
            transition_duration=duration,
            transition_requested=requested,
            transition_rationale=rationale,
            transition_expr=expression,
            # True only on the tier-3 path, where `probe` actually rendered
            # and measured it. A preset needs no proving; it is ffmpeg's.
            transition_verified=expression is not None,
        )
    )


def overlay(
    plan: Plan,
    source: str,
    x: int = 0,
    y: int = 0,
    width: int | None = None,
    height: int | None = None,
    start: str | None = None,
    end: str | None = None,
    mask: str | None = None,
    mask_source: str | None = None,
    mask_radius: int = 40,
    mask_invert: bool = False,
    mask_feather: float = 0.0,
    to_x: int | None = None,
    to_y: int | None = None,
    to_width: int | None = None,
    to_height: int | None = None,
    move_at: str | None = None,
    move_over: float = 1.0,
    easing: str = "linear",
) -> Plan:
    """Lay another clip over the picture, at a stated place and time.

    Placement is in pixels of the edit's own frame, with the origin at the top
    left. `width`/`height` resize the layer and must be given together.
    `start`/`end` bound the window it is on screen for; omit both and it runs
    for the whole edit.

    Picture only. The layer's sound is not taken: in the common
    picture-in-picture case the base already carries the narration, and adding
    a second copy of it is the defect rather than the feature.
    """
    from vid.plan import Mask, Motion, Overlay
    from vid.timecode import parse_timecode

    if (width is None) != (height is None):
        raise VidError(
            "An overlay needs both --width and --height, or neither. Given only one, the "
            "other would have to be invented from an aspect ratio nobody stated."
        )
    if width is not None and width <= 0:
        raise VidError(f"An overlay's width must be positive, not {width}.")
    if height is not None and height <= 0:
        raise VidError(f"An overlay's height must be positive, not {height}.")

    begins = parse_timecode(start) if start else None
    ends = parse_timecode(end) if end else None
    if begins is not None and ends is not None and ends <= begins:
        raise VidError(
            f"An overlay's --end ({ends:g}s) must come after its --start ({begins:g}s). "
            "As written the layer would never be on screen."
        )

    shape = None
    if mask is not None:
        allowed = ("rect", "rounded_rect", "circle", "ellipse", "image", "video")
        if mask not in allowed:
            raise VidError(f"Unknown mask {mask!r}. Use one of: {', '.join(allowed)}.")
        if mask in ("image", "video") and not mask_source:
            raise VidError(
                f"A {mask} mask reads its matte from a file, so --mask-source is required. "
                "The procedural shapes (rect, rounded_rect, circle, ellipse) need no file."
            )
        if mask_feather < 0:
            raise VidError(f"A mask's feather cannot be negative, and {mask_feather:g} is.")
        if mask_radius < 0:
            raise VidError(f"A rounded rectangle's radius cannot be negative, and {mask_radius} is.")
        shape = Mask(kind=mask, source=mask_source, radius=mask_radius, invert=mask_invert, feather=mask_feather)

    travel = None
    if any(value is not None for value in (to_x, to_y, to_width, to_height)):
        if easing not in ("linear", "ease_in_out"):
            raise VidError(f"Unknown easing {easing!r}. Use `linear` or `ease_in_out`.")
        if move_over <= 0:
            raise VidError(f"An overlay's --move-over must be positive, not {move_over:g}.")
        if (to_width is None) != (to_height is None):
            raise VidError(
                "An overlay's motion needs both --to-width and --to-height, or neither, for the "
                "same reason --width and --height do: one alone invents an aspect ratio."
            )
        travel = Motion(
            to_x=to_x if to_x is not None else x,
            to_y=to_y if to_y is not None else y,
            to_width=to_width,
            to_height=to_height,
            start=parse_timecode(move_at) if move_at else 0.0,
            duration=move_over,
            easing=easing,
        )

    return plan.with_operation(
        Overlay(
            source=source,
            x=x,
            y=y,
            width=width,
            height=height,
            start=begins,
            end=ends,
            mask=shape,
            motion=travel,
        )
    )


def caption(plan: Plan, subtitles: str, style: str | None = None) -> Plan:
    """Burn subtitles into the picture from a subtitle file."""
    from vid.plan import Caption

    return plan.with_operation(Caption(subtitles=subtitles, style=style))


def show(plan: Plan) -> Plan:
    """The edit, without performing it. The plan already is its own display."""
    return plan


def vignette(plan: Plan, strength: float = 0.35) -> Plan:
    """Darken the edges, to pull an eye to the middle."""
    from vid.plan import Vignette

    return plan.with_operation(Vignette(strength=strength))


def grade(plan: Plan | None, look: str | None = None, *, show: bool = False) -> Plan | str:
    """Apply a named look to `plan`, or (with `show=True`) list the catalogue.

    `plan` may be `None` when `show` is True, since nothing is read or written
    in that case -- only the catalogue text comes back.
    """
    from vid.looks import catalogue
    from vid.plan import Grade

    if show:
        return catalogue()
    if not look:
        raise VidError("Name a look with --look, or run `vid grade --list` to see them.")
    if plan is None:
        raise VidError("grade needs a plan to add the look to.")
    return plan.with_operation(Grade(look=look))


def lut(plan: Plan, table: str) -> Plan:
    """Apply a .cube lookup table you already have."""
    from vid.plan import Lut

    return plan.with_operation(Lut(path=table))


def transitions(describe: bool = False) -> str:
    """Every transition this tool can use, and (with `describe`) what each looks like."""
    from vid.transitions import FEEL, PRESETS

    if not describe:
        return " ".join(PRESETS)

    width = max(len(name) for name in PRESETS)
    lines = [f"  {name:<{width}}  {FEEL.get(name, '')}" for name in PRESETS]
    lines.append("")
    lines.append('  Or describe what you want -- `--transition "soft and dreamy"` -- and a model picks.')
    return "\n".join(lines)


# endregion
