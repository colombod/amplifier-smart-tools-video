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

Edit and curate video: trim, retime, zoom, stitch, caption, and find moments by
what was said or shown. Chainable — every verb passes an edit plan, and one
render compiles it to a single ffmpeg pass.

## Install

```bash
uv tool install git+https://github.com/colombod/amplifier-smart-tools-video
```

With the optional extras (`vid check` says which are present and what each one
enables — do not rely on this file for that):

```bash
uv tool install 'vid[all] @ git+https://github.com/colombod/amplifier-smart-tools-video'
```

As a library from another project:

```bash
uv add "vid @ git+https://github.com/colombod/amplifier-smart-tools-video"
```

Already installed? `uv tool upgrade vid`.

## Read this before running anything

**`vid --help` prints the real instructions** — every verb, worked invocations,
the sharp edges, and how to chain. It is written for you, not for a human
skimming a man page. Read it first, then confirm each argument with
`vid <verb> --help` rather than from memory. Run `vid check` and follow what
it reports before telling a user something is impossible.
