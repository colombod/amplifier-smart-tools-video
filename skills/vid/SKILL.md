---
name: vid
description: >-
  Anything to do with a video file the user has — .mp4, .mov, .mkv, .webm.
  Reach for it when the ask sounds like "cut this down to the bit where she
  explains pricing", "add captions to this", "make this shorter", "stick these
  three clips together", "speed up the boring middle", "put some music under
  it", "where does he mention the deadline?", or "make this match our brand
  colours". Trims and cuts, joins clips with transitions, retimes and ramps
  speed, zooms, burns in captions, removes/replaces/mixes audio, grades colour
  or matches a reference image, vignettes, writes and speaks a narration fitted
  to the video's own timing, finds a moment by what was SAID or SHOWN, and
  verifies a finished render. Chain the verbs with pipes — the whole edit is one
  ffmpeg pass. Do NOT use for images, audio-only files, or downloading video.
license: MIT
metadata:
  repository: https://github.com/colombod/amplifier-smart-tools-video
---

# vid

## Install

```bash
uv tool install git+https://github.com/colombod/amplifier-smart-tools-video
```

With local transcription, so moments can be found by what was said, and local
speech so narration can be spoken:

```bash
uv tool install 'vid[all] @ git+https://github.com/colombod/amplifier-smart-tools-video'
```

As a library from another project — the CLI is a thin wrapper, and `vid.lib`
holds every capability:

```bash
uv add "vid @ git+https://github.com/colombod/amplifier-smart-tools-video"
```

Already installed? `uv tool upgrade vid`. `vid --version` against the repository's
latest tag says whether that is worth doing.

## Read this before running anything

**`vid --help` prints the real instructions** — every verb, worked invocations,
the sharp edges, and how to chain. It is written for you, not for a human
skimming a man page. Read it first, then confirm each argument with
`vid <verb> --help` rather than from memory.

**`vid check` says what THIS machine can actually do.** Some capabilities need
ffmpeg, some need a local model, some need a provider — and it reports exactly
which are available and the command to fix each gap. Run it before telling a user
something is impossible.

## The one thing worth knowing up front

**Chain the verbs. Do not call them one at a time.**

Every verb except `render` takes an edit plan on stdin, adds one operation, and
writes the plan back out. Nothing touches a frame until `render`, which compiles
the whole chain into a single ffmpeg pass:

```bash
vid trim talk.mp4 --from 0:10 --to 2:30 \
  | vid retime --speed 1.5x \
  | vid grade --look warm \
  | vid render out.mp4
```

That is four operations, **one decode and one encode**. Rendering between each
step instead is the obvious approach and the expensive one: four times the work,
and the picture degrades at every generation.

Composing the chain needs no model and costs nothing — a plan is JSON. Decide the
edit, write the pipeline, run it once.
