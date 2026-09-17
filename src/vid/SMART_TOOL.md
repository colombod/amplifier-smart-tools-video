---
smart_tool_format: 1
name: vid
version: 0.1.0
description: >-
  Edit and curate video: trim, retime, zoom, stitch, caption, and find moments by what was said or shown. Chainable — every verb passes an edit plan, and one render compiles it to a single ffmpeg pass
use_cases:
  - >-
    Edit and curate video: trim, retime, zoom, stitch, caption, and find moments by what was said or shown. Chainable — every verb passes an edit plan, and one render compiles it to a single ffmpeg pass
platforms:
  - linux
  - macos
  - windows
requires:
  - name: gh
    purpose: >-
      Generates the token that signs in to GitHub Copilot. Without it, the model-backed
      capabilities cannot authenticate.
    optional: true
    install: https://cli.github.com/
  - name: github-copilot-subscription
    purpose: >-
      A Copilot subscription on the account signed in to gh powers the model-backed
      capabilities. Without it, only the deterministic capabilities run.
    optional: true
    install: https://github.com/github/copilot-cli#prerequisites
---

Edit and curate video: trim, retime, zoom, stitch, caption, and find moments by what was said or shown. Chainable — every verb passes an edit plan, and one render compiles it to a single ffmpeg pass.

**The library is the tool.** `vid.lib` holds every capability. The CLI is a thin
wrapper over it, so anything you can do from the shell you can also do from Python.

## Read this first: chain the verbs, do not orchestrate them

**Every verb except `render` reads an edit plan on stdin, appends one operation,
and writes the plan to stdout.** Nothing decodes a frame until `render`, which
compiles the whole plan into ONE ffmpeg pass.

```bash
vid trim talk.mp4 --from 0:10 --to 2:30 \
  | vid retime --ramp "1x@0 0.25x@1:05 1x@1:12" \
  | vid zoom --to 1.4 --at 0:45 \
  | vid stitch - outro.mp4 --transition dissolve --duration 0.8 \
  | vid render out.mp4
```

That is five operations, **one decode and one encode**.

**Do not call these verbs one at a time, rendering between them.** It is the
obvious approach and it is the expensive one: five renders means five decodes and
five encodes, it takes several times longer, and the picture loses quality at
every generation. The pipe exists precisely so you never have to do that.

**You do not need a model in this loop.** Composing an edit is the shell's job,
not an agent's. Every verb in that chain is deterministic, instant, costs
**$0.00**, and needs neither ffmpeg nor any AI provider — a plan is JSON. Decide
the edit once, write the pipeline, run it.

Three rules that make chains predictable:

- **Start a chain by naming a file; continue one by piping.** `vid trim talk.mp4`
  begins. `vid zoom --to 1.4` continues whatever arrived on stdin. A verb given
  neither fails and says so.
- **`-` means "the plan on stdin"**, and it holds a position. `vid stitch intro.mp4
  - outro.mp4` puts the running edit in the middle.
- **Inspect before you commit.** `vid plan` prints the edit as JSON; `vid render
  out.mp4 --print-command` prints the exact ffmpeg and runs nothing. A plan can be
  saved, diffed, hand-edited and replayed, so an edit a model proposed is as
  reviewable as one a person typed.

## When to reach for it

- Edit and curate video: trim, retime, zoom, stitch, caption, and find moments by what was said or shown. Chainable — every verb passes an edit plan, and one render compiles it to a single ffmpeg pass.

## Before writing code

Confirm every capability and argument against `vid <command> --help` before using it.
Do not fill gaps from memory. The library source beside this file, `lib.py`, carries the
signatures. The repository's `docs/01-library.md` and `docs/02-cli.md` carry the rest.

## Install

```bash
# as a CLI
uv tool install git+https://github.com/colombod/amplifier-smart-tools-video

# as a library, from another project
uv add "vid @ git+https://github.com/colombod/amplifier-smart-tools-video"

# once, without installing
uvx --from git+https://github.com/colombod/amplifier-smart-tools-video vid --help
```
Verify with `vid manifest`, which needs no credentials.

## Prerequisites

Deterministic capabilities need only `uv`. Model-backed capabilities run through GitHub
Copilot, signed in as the GitHub CLI's user: `gh` must be installed and `gh auth login`
completed with an account that has a Copilot subscription. Without that, a model-backed
capability fails immediately and names what to configure; it never falls back to a
deterministic answer.

Runs on Linux, macOS, and Windows.

## Straight and smart paths

Deterministic capabilities run with no provider configured. Model-backed capabilities go
through GitHub Copilot, signed in as the GitHub CLI's user, and say so in their help text.

## Output and failure contract

Results go to stdout, diagnostics to stderr. A failure prints a message naming what went
wrong and how to fix it, and exits non-zero: 1 for a failure the tool can name, 2 for a
bad invocation. Never treat an empty result as success.

## Choosing a surface

Import the library from Python. Shell out to the CLI from anything that cannot import
Python in-process: a shell script, a CI job, or an agent that can run commands but not
load a Python object. Both reach the same capabilities.
