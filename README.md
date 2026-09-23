# Vid Smart Tool

[Explore the website](https://colombod.github.io/amplifier-smart-tools-video/)

Edit and curate video: trim, retime, zoom, stitch, caption, and find moments by what was said or shown. Chainable — every verb passes an edit plan, and one render compiles it to a single ffmpeg pass.

Vid is a [Smart Tool](https://github.com/microsoft/amplifier-smart-tools): a library with a thin CLI over it, whose model-backed capabilities sit behind an interface.

## Installation

Prerequisites:
- [uv](https://docs.astral.sh/uv/getting-started/installation/).
- [GitHub CLI](https://cli.github.com/) signed in to an account with a [GitHub Copilot subscription](https://github.com/github/copilot-cli#prerequisites) for the model-backed capabilities.

```bash
uv tool install git+https://github.com/colombod/amplifier-smart-tools-video
```

With the optional extras:

```bash
uv tool install 'vid[all] @ git+https://github.com/colombod/amplifier-smart-tools-video'
```

`all` is all three below. Run `vid check` to see which are present and
configured on your machine.

### speech

Local transcription, so `index --speech` can build timed passages and `find`
can search what was *said*. Runs on your machine; nothing is uploaded.

```bash
uv tool install 'vid[speech] @ git+https://github.com/colombod/amplifier-smart-tools-video'
```

### voice

Local speech for `narrate`, via piper. **This is the default**, it runs on your
machine, and the voice model is fetched once, anonymously.

```bash
uv tool install 'vid[voice] @ git+https://github.com/colombod/amplifier-smart-tools-video'
```

### voice-openai

An **alternative** to `voice`: speaks `narrate` through OpenAI's TTS API. It
**sends the narration text over the network and costs money**, so it is used
only when you ask for it by name with `--voice openai:<voice>` — piper being
missing never silently falls back to it.

```bash
uv tool install 'vid[voice-openai] @ git+https://github.com/colombod/amplifier-smart-tools-video'
```

It also needs an API key. Name the environment *variable* holding it, per
profile, in `$XDG_CONFIG_HOME/vid/config.toml` — so several keys can coexist
without renaming any of them:

```toml
[providers.openai.work]
api_key_env = "WORK_OPENAI_KEY"
```

Then `--voice openai:alloy@work`. With no config file at all, `OPENAI_API_KEY`
is used. The file never holds a key itself, only the name of the variable to
read, so it stays safe to share or commit.

OpenAI speech defaults to `gpt-4o-mini-tts`; an explicit `model` in that profile
overrides it. This is separate from narration's script model, selected with
`--intelligence-model` and `--reasoning-effort`. Piper remains the local default.

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
