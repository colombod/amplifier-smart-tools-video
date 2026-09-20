This is a Smart Tool that must conform to Microsoft's [Amplifier Smart Tool Spec](https://github.com/microsoft/amplifier-smart-tools).

## Orientation

- If not already, set up the environment by following CONTRIBUTING.md. It also has every command for linting, formatting, type checking, testing, and running the conformance kit.
- After making changes, `prek run --all-files` and `uv run pytest` must pass before the work is done.
- A local `pytest` pass does not mean CI will pass: CI's ffmpeg (`ubuntu-latest`'s apt package) is not this machine's ffmpeg, and a filter-graph change has broken on CI while passing locally before (commit bfeaa13). Before pushing an ffmpeg-facing change (`compile.py`, `probe.py`, anything touching filter graphs), run `scripts/ci-parity.sh test` -- see CONTRIBUTING.md's "CI parity" section.
- `docs/00-vision.md` is the source of truth for what this tool is and is not, and `docs/01-library.md` for what it exposes. Then `README.md` and `CONTRIBUTING.md`, then the rest of `docs/`. Both are written for people and stay concise. When work changes any of them, propose the doc updates at the end and call out contradictions.
- The library is the tool. Every capability lives in the library and is reachable from `lib.py`. The CLI and any other surface are thin wrappers: argument parsing and I/O conventions, then a call into the library. Capability that exists only in a wrapper is a defect.
- Each domain capability lives in `capabilities/<name>/`, with its prompts and templates beside its code. `lib.py` imports it and is the only place that does. `core/` holds only what every smart tool has: the manifest and the skill.
- Deterministic capabilities run with no model provider configured. Model-backed capabilities go through the `Intelligence` interface, never an SDK directly, and their help text says they are model-backed. They default to `DEFAULT_INTELLIGENCE_MODEL` and `low` reasoning effort from `schemas.py` and expose both as parameters; never hardcode a model name.
- Failures name what went wrong and how to fix it. The caller is usually an agent.
- The `Repository` URL under `[project.urls]` in `pyproject.toml` is the tool's canonical source: `--help` carries it, so an agent that can run the tool but not read its files still finds the docs, and every install instruction in `README.md`, `SMART_TOOL.md`, and the skill is built on it. If it is still the `<owner>` placeholder, replace it everywhere it appears once the remote exists.
- Never modify this file unless explictly told.

## Writing Style

- Be concise. The user will not read walls of text.
- When adding to existing documents, add only what's needed.
- Do not restructure or rewrite existing content unless asked.
- Always prefer code blocks and other formatting over tables.
- Match the tone and density of what's already in the file.
- Never write em dashes

## Python Development Instructions

- This is a production-grade Python project using `uv` as the package and project manager. You must *always* follow best Python practices.
  - To figure out how `uv` works, start by using `uv --help`.
- Make sure any comments in code are necessary. A necessary comment captures intent that cannot be encoded in names, types, or structure. Comments should be reserved for the "why", only used to record rationale, trade-offs, links to specs/papers, or non-obvious domain insights. They should add signal that code cannot.
- The current code in the package should be treated as an example of high quality code. Make sure to follow its style and tackle issues in similar ways where appropriate.
- Don't generate characters that a user could not type on a standard keyboard like fancy arrows (layout trees are fine)
- Anything is possible. Do not blame external factors after something doesn't work on the first try. Instead, investigate and test assumptions through debugging through first principles.
- `ty` by Astral is used for type checking. Always add appropriate type hints such that the code would pass ty's type check.
- Follow the Google Python Style Guide.
- NEVER add imports to __init__.py files. Leave them empty unless absolutely necessary.
- Always prefer pathlib for dealing with files. Use `Path.open` instead of `open`.
- When using pathlib, **always** Use `.parents[i]` syntax to go up directories instead of using `.parent` multiple times.
- When writing tests, use pytest and pytest-asyncio.
- Prefer using loguru for logging instead of the built-in logging module. Do not add logging unless requested.
- NEVER use `# type: ignore`. It is better to leave the issue and have the user work with you to fix it.
- Don't put types in quotes unless it is absolutely necessary to avoid circular imports and forward references.
- When adding new dependencies, you **must** use `uv add <package>`. AFTER that, update the `pyproject.toml` to follow the convention for versions like the other dependencies.
- To learn about how packages work, you should read from the relevant source code. This is especially important when determining which types to use.
- Run `prek run --all-files` when you are done with code changes and fix everything it reports; it runs the lock, lint, format, and type checks.
- NEVER add a bare `*,` keyword-only marker to a function signature. Write plain positional-or-keyword parameters and call them by keyword.

## Key Files

@CONTRIBUTING.md
@docs/00-vision.md

## References

`reference/` holds the repositories to read rather than recall while developing this tool.
They are shallow clones of the main branch only, always gitignored, and never discovered by tests, formatters, or type checks.
`uv run setup-for-dev.py` clones any that are missing, so a fresh clone of this repository recovers them.
The repositories are (add to the list as more are needed, the one exception to modifying this file):

- https://github.com/microsoft/amplifier-smart-tools
- https://github.com/github/copilot-sdk
- https://github.com/agentskills/agentskills
