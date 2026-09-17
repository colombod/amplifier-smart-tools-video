"""Command line entry point for Vid.

THIN BY CONSTRUCTION. Every command here parses arguments, calls the library, and
writes a plan. No decision about video lives in this file -- if it did, a caller
using `vid` as a Python library would not get it.

TWO HELPS PER VERB. `-h` is the terse summary Typer generates, for a person who
wants to remember a flag name. `--help` is the verb's own document, for an agent
deciding whether and how to call it. Collapsing them would serve neither.
"""

from __future__ import annotations

from typing import Annotated

import typer

from vid import lib
from vid.plan import Caption, Cut, Plan, RampPoint, Retime, Stitch, Trim, Zoom, read_plan, write_plan
from vid.schemas import VidError
from vid.timecode import parse_speed, parse_timecode
from vid.verbdoc import verb_doc

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


@app.callback()
def cli(
    help: Annotated[
        bool,
        typer.Option("--help", is_eager=True, callback=_print_skill, help="This tool's skill, for an agent driving it."),
    ] = False,
) -> None:
    """Edit and curate video: trim, retime, zoom, stitch, caption, and find moments by what was said or shown."""


@app.command()
def manifest() -> None:
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
    plan = read_plan(source)
    write_plan(plan.with_operation(Trim(start=parse_timecode(start), end=parse_timecode(end) if end else None)))


@app.command()
def cut(
    source: Annotated[str | None, typer.Argument(help="A video file, or omit to continue a piped plan.")] = None,
    start: Annotated[str, typer.Option("--from", help="Where the removed range begins.")] = ...,
    end: Annotated[str, typer.Option("--to", help="Where it ends.")] = ...,
    help: _doc("cut") = False,
) -> None:
    """Remove a time range, keeping what surrounds it."""
    plan = read_plan(source)
    write_plan(plan.with_operation(Cut(start=parse_timecode(start), end=parse_timecode(end))))


@app.command()
def retime(
    source: Annotated[str | None, typer.Argument(help="A video file, or omit to continue a piped plan.")] = None,
    speed: Annotated[str | None, typer.Option("--speed", help="A constant speed: `2x`, `0.5x`.")] = None,
    ramp: Annotated[str | None, typer.Option("--ramp", help='A curve: "1x@0 0.25x@1:05 1x@1:12".')] = None,
    help: _doc("retime") = False,
) -> None:
    """Change speed, constantly or along a curve."""
    if (speed is None) == (ramp is None):
        raise VidError("Give exactly one of --speed (a constant) or --ramp (a curve).")
    plan = read_plan(source)
    if speed is not None:
        write_plan(plan.with_operation(Retime(speed=parse_speed(speed))))
        return
    points = []
    for token in ramp.split():
        value, _, at = token.partition("@")
        if not at:
            raise VidError(f"{token!r} is not a ramp point. Write `speed@time`, as in `0.25x@1:05`.")
        points.append(RampPoint(at=parse_timecode(at), speed=parse_speed(value)))
    write_plan(plan.with_operation(Retime(ramp=points)))


@app.command()
def zoom(
    source: Annotated[str | None, typer.Argument(help="A video file, or omit to continue a piped plan.")] = None,
    to: Annotated[float, typer.Option("--to", help="Final zoom factor. 1.4 is a noticeable push.")] = 1.3,
    at: Annotated[str | None, typer.Option("--at", help="Centre the zoom on this moment.")] = None,
    duration: Annotated[float, typer.Option("--duration", help="Seconds the zoom takes.")] = 3.0,
    help: _doc("zoom") = False,
) -> None:
    """Animated zoom (Ken Burns), with the jitter fix applied."""
    plan = read_plan(source)
    write_plan(plan.with_operation(Zoom(to=to, at=parse_timecode(at) if at else None, duration=duration)))


@app.command()
def stitch(
    sources: Annotated[list[str], typer.Argument(help="Clips in order. `-` is the plan on stdin.")],
    transition: Annotated[str | None, typer.Option("--transition", help="An xfade preset: fade, dissolve, wipeleft...")] = None,
    duration: Annotated[float, typer.Option("--duration", help="Seconds the transition takes.")] = 0.5,
    help: _doc("stitch") = False,
) -> None:
    """Join clips, with or without a transition."""
    if "-" in sources:
        plan = read_plan(None)
        rest = [s for s in sources if s != "-"]
    else:
        plan = Plan(source=sources[0])
        rest = sources[1:]
    if not rest:
        raise VidError("stitch needs at least one clip to join on. Name another file.")
    write_plan(plan.with_operation(Stitch(sources=rest, transition=transition, transition_duration=duration)))


@app.command()
def caption(
    source: Annotated[str | None, typer.Argument(help="A video file, or omit to continue a piped plan.")] = None,
    subtitles: Annotated[str, typer.Option("--subtitles", help="An .srt or .ass file.")] = ...,
    style: Annotated[str | None, typer.Option("--style", help="libass force_style, e.g. 'FontSize=28'.")] = None,
    help: _doc("caption") = False,
) -> None:
    """Burn subtitles into the picture."""
    plan = read_plan(source)
    write_plan(plan.with_operation(Caption(subtitles=subtitles, style=style)))


@app.command(name="plan")
def show_plan(help: _doc("plan") = False) -> None:
    """Show the edit, without performing it."""
    write_plan(read_plan(None))


@app.command()
def render(
    output: Annotated[str, typer.Argument(help="Where to write the finished video.")],
    print_command: Annotated[bool, typer.Option("--print-command", help="Print the ffmpeg it would run, and stop.")] = False,
    help: _doc("render") = False,
) -> None:
    """Compile the plan and encode, once."""
    result = lib.render(read_plan(None), output, print_command=print_command)
    typer.echo(result)


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
