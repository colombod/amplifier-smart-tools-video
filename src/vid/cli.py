"""Command line entry point for Vid.

THIN BY CONSTRUCTION. Every command here parses arguments, reads the plan (or
transport input) that command needs, calls exactly one `vid.lib` function, and
writes what comes back. No decision about video lives in this file -- if it
did, a caller using `vid` as a Python library would not get it.

TWO HELPS PER VERB. `-h` is the terse summary Typer generates, for a person who
wants to remember a flag name. `--help` is the verb's own document, for an agent
deciding whether and how to call it. Collapsing them would serve neither.
"""

from __future__ import annotations

from typing import Annotated

import typer

from vid import lib
from vid.plan import read_plan, write_plan
from vid.schemas import DEFAULT_INTELLIGENCE_MODEL, ReasoningEffort, VidError
from vid.verbdoc import verb_doc

ModelOption = Annotated[str, typer.Option("--model", "--intelligence-model", help="Model for model-backed work.")]
EffortOption = Annotated[
    ReasoningEffort, typer.Option("--reasoning-effort", help="Reasoning effort for model-backed work.")
]

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _print_skill(value: bool) -> None:
    if value:
        typer.echo(lib.skill())
        raise typer.Exit()


def _doc(name: str):
    """A `--help` that prints this verb's document and stops."""

    def callback(value: bool) -> None:
        if value:
            typer.echo(verb_doc(name))
            raise typer.Exit()

    return Annotated[
        bool,
        typer.Option("--help", is_eager=True, callback=callback, help="This verb, for an agent driving it."),
    ]


def _print_version(value: bool) -> None:
    """`--version`, which the skill file told callers to run before it existed.

    Found by writing a CI smoke test that ran the commands our own documentation
    recommends. The skill said "`vid --version` against the repository's latest
    tag says whether an upgrade is worth doing" -- and the flag exited 2. Nobody
    had run the instructions.
    """
    if value:
        from importlib.metadata import version

        typer.echo(version("vid"))
        raise typer.Exit()


@app.callback()
def cli(
    help: Annotated[
        bool,
        typer.Option(
            "--help", is_eager=True, callback=_print_skill, help="This tool's skill, for an agent driving it."
        ),
    ] = False,
    version: Annotated[
        bool,
        typer.Option("--version", is_eager=True, callback=_print_version, help="The installed version."),
    ] = False,
) -> None:
    """Edit and curate video: trim, retime, zoom, stitch, caption, and find moments by what was said or shown."""


@app.command()
def manifest(
    help: _doc("manifest") = False,
) -> None:
    """Print the tool's manifest as JSON. Deterministic."""
    typer.echo(lib.load_manifest().model_dump_json(indent=2))


@app.command()
def trim(
    source: Annotated[str | None, typer.Argument(help="A video file, or omit to continue a piped plan.")] = None,
    start: Annotated[str, typer.Option("--from", help="Where to start keeping. `90`, `1:30`, `0:10.5`.")] = "0",
    end: Annotated[str | None, typer.Option("--to", help="Where to stop. Omit to run to the end.")] = None,
    help: _doc("trim") = False,
) -> None:
    """Keep a time range, discard the rest."""
    write_plan(lib.trim(read_plan(source), start, end))


@app.command()
def cut(
    source: Annotated[str | None, typer.Argument(help="A video file, or omit to continue a piped plan.")] = None,
    start: Annotated[str, typer.Option("--from", help="Where the removed range begins.")] = ...,
    end: Annotated[str, typer.Option("--to", help="Where it ends.")] = ...,
    help: _doc("cut") = False,
) -> None:
    """Remove a time range, keeping what surrounds it."""
    write_plan(lib.cut(read_plan(source), start, end))


@app.command()
def retime(
    source: Annotated[str | None, typer.Argument(help="A video file, or omit to continue a piped plan.")] = None,
    speed: Annotated[str | None, typer.Option("--speed", help="A constant speed: `2x`, `0.5x`.")] = None,
    ramp: Annotated[str | None, typer.Option("--ramp", help='A curve: "1x@0 0.25x@1:05 1x@1:12".')] = None,
    help: _doc("retime") = False,
) -> None:
    """Change speed, constantly or along a curve."""
    write_plan(lib.retime(read_plan(source), speed, ramp))


@app.command()
def zoom(
    source: Annotated[str | None, typer.Argument(help="A video file, or omit to continue a piped plan.")] = None,
    to: Annotated[float, typer.Option("--to", help="Final zoom factor. 1.4 is a noticeable push.")] = 1.3,
    at: Annotated[str | None, typer.Option("--at", help="Centre the zoom on this moment.")] = None,
    duration: Annotated[float, typer.Option("--duration", help="Seconds the zoom takes.")] = 3.0,
    help: _doc("zoom") = False,
) -> None:
    """Animated zoom (Ken Burns), with `zoompan`'s duration and size bugs fixed."""
    write_plan(lib.zoom(read_plan(source), to, at, duration))


@app.command()
def stitch(
    sources: Annotated[list[str], typer.Argument(help="Clips in order. `-` is the plan on stdin.")],
    transition: Annotated[
        str | None, typer.Option("--transition", help="An xfade preset: fade, dissolve, wipeleft...")
    ] = None,
    duration: Annotated[float, typer.Option("--duration", help="Seconds the transition takes.")] = 0.5,
    fit: Annotated[
        str | None,
        typer.Option("--fit", help="Resolve a differing size: `fit` pads with bars, `fill` crops centred."),
    ] = None,
    help: _doc("stitch") = False,
    model: ModelOption = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: EffortOption = "low",
) -> None:
    """Join clips, with or without a transition."""
    if "-" in sources:
        plan, rest = read_plan(None), [s for s in sources if s != "-"]
    else:
        plan, rest = None, sources
    write_plan(
        lib.stitch(
            plan,
            rest,
            transition=transition,
            duration=duration,
            fit=fit,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    )


@app.command()
def overlay(
    source: Annotated[str, typer.Argument(help="The clip to lay over the picture.")],
    over: Annotated[str | None, typer.Argument(help="A video file, or omit to continue a piped plan.")] = None,
    x: Annotated[int, typer.Option("--x", help="Left edge, in pixels from the frame's left.")] = 0,
    y: Annotated[int, typer.Option("--y", help="Top edge, in pixels from the frame's top.")] = 0,
    width: Annotated[int | None, typer.Option("--width", help="Resize the layer. Needs --height too.")] = None,
    height: Annotated[int | None, typer.Option("--height", help="Resize the layer. Needs --width too.")] = None,
    start: Annotated[str | None, typer.Option("--start", help="When the layer appears.")] = None,
    end: Annotated[str | None, typer.Option("--end", help="When the layer disappears.")] = None,
    help: _doc("overlay") = False,
) -> None:
    """Lay another clip over the picture, at a stated place and time."""
    write_plan(lib.overlay(read_plan(over), source, x, y, width, height, start, end))


@app.command()
def caption(
    source: Annotated[str | None, typer.Argument(help="A video file, or omit to continue a piped plan.")] = None,
    subtitles: Annotated[str, typer.Option("--subtitles", help="An .srt or .ass file.")] = ...,
    style: Annotated[str | None, typer.Option("--style", help="libass force_style, e.g. 'FontSize=28'.")] = None,
    help: _doc("caption") = False,
) -> None:
    """Burn subtitles into the picture."""
    write_plan(lib.caption(read_plan(source), subtitles, style))


@app.command(name="plan")
def show_plan(help: _doc("plan") = False) -> None:
    """Show the edit, without performing it."""
    write_plan(lib.show(read_plan(None)))


@app.command()
def render(
    output: Annotated[str, typer.Argument(help="Where to write the finished video.")],
    print_command: Annotated[
        bool, typer.Option("--print-command", help="Print the ffmpeg it would run, and stop.")
    ] = False,
    help: _doc("render") = False,
    video_codec: Annotated[
        str, typer.Option("--video-codec", help="libx264 (default), or guarded picture copy.")
    ] = "libx264",
) -> None:
    """Compile the plan and encode, once."""
    result = lib.render(read_plan(None), output, print_command=print_command, video_codec=video_codec)
    typer.echo(result)


@app.command(name="index")
def build_index(
    video: Annotated[str, typer.Argument(help="The video to index.")],
    speech: Annotated[bool, typer.Option("--speech/--no-speech", help="Transcribe the audio.")] = True,
    model_size: Annotated[str, typer.Option("--model", help="Whisper size: tiny, base, small, medium.")] = "base",
    vision: Annotated[bool, typer.Option("--vision", help="Also describe what is SHOWN, one frame per shot.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask before spending on descriptions.")] = False,
    help: _doc("index") = False,
    model: Annotated[
        str, typer.Option("--intelligence-model", help="Model for vision descriptions, NOT Whisper size.")
    ] = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: EffortOption = "low",
) -> None:
    """Build a time-coded account of what is in a video. Reused by every later question."""
    typer.echo(
        lib.index(
            video,
            speech=speech,
            model_size=model_size,
            vision=vision,
            yes=yes,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    )


@app.command()
def find(
    query: Annotated[str, typer.Argument(help="What to look for, in words.")],
    video: Annotated[str, typer.Argument(help="The video to search.")],
    show: Annotated[bool, typer.Option("--show", help="Print the hits and their evidence instead of a plan.")] = False,
    help: _doc("find") = False,
    model: ModelOption = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: EffortOption = "low",
) -> None:
    """Locate a moment by what was said. Writes a plan trimmed to it."""
    lib.find(query, video, show=show, model=model, reasoning_effort=reasoning_effort)


audio_app = typer.Typer(
    help="Remove, replace, mix or extract the audio track.",
    no_args_is_help=True,
)
app.add_typer(audio_app, name="audio")


@audio_app.callback()
def _audio_group(help: _doc("audio") = False) -> None:
    """Remove, replace, mix or extract the audio track.

    A group callback, because `audio` is a sub-app rather than a verb. Without
    this, `vid audio --help` fell through to Typer's usage box while every other
    verb answered with its document -- the one inconsistency in the surface, and
    exactly the kind an agent trips on: it asks every verb the same question and
    gets a different KIND of answer from one of them.
    """


@audio_app.command("remove")
def audio_remove(
    video: Annotated[str | None, typer.Argument(help="The video, or omit to continue a piped plan.")] = None,
    help: _doc("audio_remove") = False,
) -> None:
    """Drop the audio. The result is a silent video."""
    write_plan(lib.audio_remove(read_plan(video)))


@audio_app.command("replace")
def audio_replace(
    video: Annotated[str | None, typer.Argument(help="The video, or omit to continue a piped plan.")] = None,
    with_: Annotated[str, typer.Option("--with", help="The audio file to use instead.")] = ...,
    help: _doc("audio_replace") = False,
    start: Annotated[
        float, typer.Option("--start", help="Place the supplied track at this second in the current edit.")
    ] = 0.0,
) -> None:
    """Swap the audio track for another file's. Result is always the video's length."""
    write_plan(lib.audio_replace(read_plan(video), with_, start=start))


@audio_app.command("mix")
def audio_mix(
    video: Annotated[str | None, typer.Argument(help="The video, or omit to continue a piped plan.")] = None,
    with_: Annotated[str, typer.Option("--with", help="The track to lay underneath.")] = ...,
    level: Annotated[
        float, typer.Option("--level", help="dB applied to the incoming track. Negative puts it under.")
    ] = -18.0,
    help: _doc("audio_mix") = False,
    start: Annotated[
        float, typer.Option("--start", help="Place the supplied track at this second in the current edit.")
    ] = 0.0,
) -> None:
    """Lay another track under the existing audio, keeping both."""
    write_plan(lib.audio_mix(read_plan(video), with_, level, start=start))


@audio_app.command("extract")
def audio_extract(
    video: Annotated[str, typer.Argument(help="The video to take audio from.")],
    output: Annotated[str, typer.Argument(help="Where to write it, e.g. out.wav.")],
    help: _doc("audio_extract") = False,
) -> None:
    """Pull the audio out to a file. Ends a chain rather than continuing one."""
    typer.echo(lib.audio_extract(video, output))


@app.command()
def narrate(
    video: Annotated[str, typer.Argument(help="The video to narrate. Index it first.")],
    prompt: Annotated[str, typer.Argument(help="What the narration is for, in your words.")],
    out: Annotated[
        str | None, typer.Option("--out", help="Render here. Omit to get the track and the command.")
    ] = None,
    script_only: Annotated[
        bool, typer.Option("--script-only", help="Print the script as JSON; synthesise nothing.")
    ] = False,
    voice: Annotated[
        str | None,
        typer.Option(
            "--voice",
            help=(
                "A piper voice model name (default, local, free), or 'openai:<voice>[@profile]' "
                "to use OpenAI's TTS instead -- needs a key configured and sends narration "
                "text to OpenAI."
            ),
        ),
    ] = None,
    mix: Annotated[
        bool | None, typer.Option("--mix/--replace", help="Over the original audio, or instead of it.")
    ] = None,
    help: _doc("narrate") = False,
    model: ModelOption = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: EffortOption = "low",
) -> None:
    """Write a narration, fit it to the timing of the video, and lay it on."""
    typer.echo(
        lib.narrate(
            video,
            prompt,
            out=out,
            script_only=script_only,
            voice=voice,
            mix=mix,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    )


@app.command()
def recolor(
    video: Annotated[str | None, typer.Argument(help="The video, or omit to continue a piped plan.")] = None,
    like: Annotated[str, typer.Option("--like", help="A reference image to take the palette from.")] = ...,
    strength: Annotated[float, typer.Option("--strength", help="0 leaves it alone, 1 is the full transfer.")] = 1.0,
    help: _doc("recolor") = False,
) -> None:
    """Map the video's colour onto a reference image's. No model, no network."""
    write_plan(lib.recolor(read_plan(video), like, strength))


@app.command()
def vignette(
    video: Annotated[str | None, typer.Argument(help="The video, or omit to continue a piped plan.")] = None,
    strength: Annotated[
        float,
        typer.Option("--strength", help="0 is nothing, 1 is theatrical. Default reads without announcing itself."),
    ] = 0.35,
    help: _doc("vignette") = False,
) -> None:
    """Darken the edges, to pull an eye to the middle."""
    write_plan(lib.vignette(read_plan(video), strength))


@app.command()
def grade(
    video: Annotated[str | None, typer.Argument(help="The video, or omit to continue a piped plan.")] = None,
    look: Annotated[str | None, typer.Option("--look", help="warm, cool, punchy, flat, noir, soft, bright.")] = None,
    show: Annotated[bool, typer.Option("--list", help="Show the looks and what each is for.")] = False,
    help: _doc("grade") = False,
) -> None:
    """Apply a named look to the whole clip."""
    result = lib.grade(None if show else read_plan(video), look, show=show)
    if isinstance(result, str):
        typer.echo(result)
    else:
        write_plan(result)


@app.command()
def lut(
    table: Annotated[str, typer.Argument(help="A .cube lookup table.")],
    video: Annotated[str | None, typer.Argument(help="The video, or omit to continue a piped plan.")] = None,
    help: _doc("lut") = False,
) -> None:
    """Apply a .cube lookup table you already have."""
    write_plan(lib.lut(read_plan(video), table))


@app.command()
def verify(
    video: Annotated[str, typer.Argument(help="The rendered file to check.")],
    expect_duration: Annotated[
        float | None, typer.Option("--expect-duration", help="Seconds the result should be.")
    ] = None,
    tolerance: Annotated[float, typer.Option("--tolerance", help="How far off the duration may be.")] = 0.15,
    expect_resolution: Annotated[str | None, typer.Option("--expect-resolution", help="e.g. 1920x1080.")] = None,
    expect_audio: Annotated[bool, typer.Option("--expect-audio", help="Audio present, and not silent.")] = False,
    expect_transition_at: Annotated[
        float | None, typer.Option("--expect-transition-at", help="A real blend at this second.")
    ] = None,
    expect_no_black_frames: Annotated[
        bool, typer.Option("--expect-no-black-frames", help="No long black stretches.")
    ] = False,
    longest_black: Annotated[
        float, typer.Option("--longest-black", help="Seconds of black that is still acceptable.")
    ] = 0.5,
    help: _doc("verify") = False,
    pixel_threshold: Annotated[float, typer.Option("--pixel-threshold", help="Black pixel luma fraction, 0..1.")] = 0.1,
    frame_threshold: Annotated[
        float, typer.Option("--frame-threshold", help="Fraction of black pixels required, 0..1.")
    ] = 0.98,
) -> None:
    """Check a rendered video against what you expected. No model involved."""
    passed, text = lib.verify(
        video,
        expect_duration=expect_duration,
        tolerance=tolerance,
        expect_resolution=expect_resolution,
        expect_audio=expect_audio,
        expect_transition_at=expect_transition_at,
        expect_no_black_frames=expect_no_black_frames,
        longest_black=longest_black,
        pixel_threshold=pixel_threshold,
        frame_threshold=frame_threshold,
    )
    typer.echo(text)
    if not passed:
        raise typer.Exit(code=1)


@app.command()
def transitions(
    describe: Annotated[bool, typer.Option("--describe", help="Show what each one looks like.")] = False,
    help: _doc("transitions") = False,
) -> None:
    """Every transition this tool can use, and what each looks like."""
    typer.echo(lib.transitions(describe=describe))


@app.command()
def check(help: _doc("check") = False) -> None:
    """What this installation can actually do."""
    typer.echo(lib.check())


def main() -> int:
    try:
        app()
    except VidError as error:
        typer.echo(f"vid: {error}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
