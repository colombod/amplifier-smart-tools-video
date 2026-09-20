# Contributing to Vid

## Development Setup

### Prerequisites

Install:

- [Git](https://git-scm.com/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/): Manages Python environments
- [prek](https://github.com/j178/prek): Used for precommit hooks. Recommended to install through PyPI/uv with `uv tool install prek`. Use `uv tool upgrade prek` to update it.
- [GitHub CLI](https://cli.github.com/) for intelligence features with GitHub Copilot.
- [GitHub Copilot subscription](https://github.com/github/copilot-cli#prerequisites) for intelligent features.

### Initial Setup

1. Clone this repository and change into it.

1. Run the development installation script (sets up the uv env, precommit hooks, and the `reference/` clones):

   ```bash
   uv run setup-for-dev.py
   ```

### Essential Development Commands

*Commands should be run from the repository root, unless otherwise specified.*

#### Precommit hooks

Setup precommit hooks:

```bash
prek install
```

Run precommit hooks manually:

```bash
prek run --all-files
```

#### Python Library Development

Create uv virtual environment and install dependencies:

```bash
uv sync --frozen --all-extras --all-groups
```

To update dependencies and the lock file:

```bash
uv sync -U --all-extras --all-groups
```

Lint code:

```bash
uv run ruff check --fix --config pyproject.toml
```

Format code (also formats code blocks in .md files):

```bash
uv run ruff format --config pyproject.toml
```

Type check:

```bash
uv run ty check .
```

Run tests:

```bash
uv run pytest
```

#### References

`reference/` holds gitignored clones of the repositories worth reading while developing this tool. `uv run setup-for-dev.py` clones any that are missing.

#### Conformance

Run the spec's [conformance kit](https://github.com/microsoft/amplifier-smart-tools/tree/main/conformance) against this repository.
The outer `uv run` puts this project's `vid` on `PATH` for the kit to invoke; the inner one runs the kit with its own inline dependencies:

```bash
uv run -- uv run --no-project https://raw.githubusercontent.com/microsoft/amplifier-smart-tools/main/conformance/run.py .
```

#### CI parity

A dev machine's ffmpeg is not CI's ffmpeg. `.github/workflows/ci.yml` installs
ffmpeg via `apt-get install -y ffmpeg` on `ubuntu-latest`; a newer local build can
accept a filter graph that stable ffmpeg rejects outright (this happened: exit
234 on 6.1.1, invisible locally, immediate on CI). "Green here" only means
"green on CI" when both machines run the same ffmpeg, so run the real gates in
a container built to match:

```bash
scripts/ci-parity.sh          # everything: prek, type-check, test-job replica
scripts/ci-parity.sh test     # just the `test` CI job replica (ruff + pytest)
scripts/ci-parity.sh typecheck
scripts/ci-parity.sh prek
scripts/ci-parity.sh clean    # remove the image and cached volumes
```

First run builds the image and installs dependencies (a couple of minutes);
after that, dependency installs are cached in named Docker volumes and a
`test` run is dominated by pytest actually rendering video (~60s), not
reinstalling anything. See the comment header in `scripts/ci-parity.sh` for
what it creates and how to remove it.
