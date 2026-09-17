# Vid Smart Tool

Edit and curate video: trim, retime, zoom, stitch, caption, and find moments by what was said or shown. Chainable — every verb passes an edit plan, and one render compiles it to a single ffmpeg pass.

Vid is a [Smart Tool](https://github.com/microsoft/amplifier-smart-tools): a library with a thin CLI over it, whose model-backed capabilities sit behind an interface.

## Installation

Prerequisites:
- [uv](https://docs.astral.sh/uv/getting-started/installation/).
- [GitHub CLI](https://cli.github.com/) signed in to an account with a [GitHub Copilot subscription](https://github.com/github/copilot-cli#prerequisites) for the model-backed capabilities.

```bash
uv tool install git+https://github.com/colombod/amplifier-smart-tools-video
```

To use it as a library:

```bash
uv add "vid @ git+https://github.com/colombod/amplifier-smart-tools-video"
```

To run it once without installing:

```bash
uvx --from git+https://github.com/colombod/amplifier-smart-tools-video vid --help
```

To teach a coding agent how to use it, install the [skill](skills/vid/SKILL.md):

```bash
npx skills add colombod/amplifier-smart-tools-video
```

To update:

```bash
uv tool upgrade vid
npx skills update vid   # add --global if the skill was installed globally
```

To uninstall:

```bash
uv tool uninstall vid
npx skills remove vid   # add --global if the skill was installed globally
```

Verify an install with `vid manifest`, which needs no credentials.

## Interface

```bash
# Print the tool's manifest as JSON
vid manifest
```

See the [CLI reference](docs/02-cli.md) for every flag and the [library reference](docs/01-library.md) for the Python surface.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to set up your development environment.
