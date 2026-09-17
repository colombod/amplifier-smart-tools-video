"""Command line entry point for Vid."""

from typing import Annotated

import typer

from vid import lib
from vid.schemas import VidError

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    # Every command answers both flags. The root callback claims `--help` for the skill below, and Click drops a
    # help option name already taken by a parameter, which leaves the root's generated summary on `-h`.
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _print_skill(value: bool) -> None:
    """Answer the root `--help` with the skill the library composes, leaving `-h` to Typer."""
    if value:
        typer.echo(lib.skill())
        raise typer.Exit()


@app.callback()
def cli(
    help: Annotated[
        bool,
        typer.Option(
            "--help", is_eager=True, callback=_print_skill, help="This tool's skill, for an agent driving it."
        ),
    ] = False,
) -> None:
    """Edit and curate video: trim, retime, zoom, stitch, caption, and find moments by what was said or shown. Chainable — every verb passes an edit plan, and one render compiles it to a single ffmpeg pass"""


@app.command()
def manifest() -> None:
    """Print the tool's manifest as JSON. Deterministic."""
    typer.echo(lib.load_manifest().model_dump_json(indent=2))


def main() -> int:
    try:
        app()
    except VidError as error:
        typer.echo(error, err=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
