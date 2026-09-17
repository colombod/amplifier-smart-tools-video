# Changelog

Notable changes to `vid`, newest first. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are semantic.

**Why this file exists from the first release.** A sibling repository shipped fifteen
commits under one version number — including a fix to cost reporting that had been wrong on
every run it ever performed — because nothing forced the question "is this shipped?". A
version is a claim about what someone installed. This file is where that claim is kept
honest.

## 0.2.0

Four capabilities that did not exist in 0.1.0, and one defect that only a
machine without credentials could find.

### Added

- **`verify`** — check a rendered video against named properties: duration,
  resolution, audio present *and not silent*, a real blend at a given second, no
  long black stretches. Each reports the measured value beside the expected one
  and exits non-zero on violation, so it drops into a shell chain. No model is
  involved, which is the point — it is the only kind of verification you can
  trust to grade something a model produced.
- **`index` and `find`** — build a time-coded account of a video (shots, and
  speech as timed passages) and locate a moment by what was said. Literal search
  needs no provider at all; describing a moment by meaning escalates to one.
  A model is never asked for a timestamp: it picks passages *by id* from an index
  the transcriber timed, so a wrong time is unreachable rather than discouraged.
- **Generated transitions.** When no preset matches a description, one is
  written — then rendered as a short probe and *measured* before it reaches a
  plan. A generated expression that compiles but does not blend is refused, never
  silently replaced with `fade`.
- **`audio`** — `remove`, `replace`, `mix` and `extract`. The first way to touch
  sound on its own; everything else already carried audio alongside video.
  `mix --level` applies to the incoming track only, so a music bed is one
  adjustment rather than a balancing act. For `replace` and `mix` the result is
  always the video's length: short tracks are padded with silence, long ones
  truncated, and a 30-second music file cannot quietly extend a 3-second clip.

### Fixed

- **`vid check` never mentioned a provider**, while the tool's own refusal
  message told callers to run it to learn how to configure one. The instruction
  led somewhere that did not contain the answer. Found on first contact with a
  credential-free container, because every machine that builds this tool has a
  login that makes its refusal paths unreachable.
- **`find` was declared model-backed** and answers most queries with no provider
  at all. Over-stating a credential requirement is worse than under-stating a
  feature.
- **The manifest never declared `ffmpeg`**, its one hard dependency.
- **`-map` bracketed raw input streams**, which ffmpeg rejects with "Invalid
  argument". Unreachable until audio-only operations existed, because every
  earlier verb put at least one filter on the video.

### Verified

62 tests. Conformance 15/15 locally and **16/16 against the tool installed from
its git URL** in a container that had never seen the checkout.

Audio correctness measured rather than assumed, using fixtures carrying distinct
tones: a stitched crossfade really runs `acrossfade` under the blended picture;
a cut keeps both streams locked; `retime 2x` preserves pitch.

### Known limits

- `retime 2x` on a 3.0s clip produces 1.57s rather than 1.50s — a 4.7%
  overshoot, likely the retimed audio outlasting the video with nothing trimming
  to the shorter. Real, and not yet fixed.
- The vision half of `index` is designed but not built, so `find` searches speech
  only.
- Effects — vignette, colour grading, palette matching — do not exist yet.

## 0.1.0

The first working shape: nine verbs, a plan that travels between them, and one ffmpeg pass
at the end.

### The design

- **The pipe carries a plan, not pixels.** Every verb except `render` reads a plan on
  stdin, appends one operation, and writes it to stdout. Nothing decodes a frame until
  `render`, which compiles the whole plan into a single `filter_complex`.
- **A model runs at plan time, never at render time.** Where a model contributes, its
  choice is resolved into the plan once and recorded alongside what was asked for. A plan
  therefore renders identically on a machine with no credentials, and re-rendering never
  re-invokes a model.
- **A model chooses from closed sets; it does not invent.** `--transition "soft and
  dreamy"` picks one of 58 real presets, and an answer off that list is a loud error.

### Verbs

`trim` · `cut` · `retime` · `zoom` · `stitch` · `caption` · `plan` · `render` · `check` ·
`transitions` · `manifest`

Each carries two helps: `-h` is the terse summary for a person, `--help` is an
agent-facing document stating whether the verb touches pixels, whether it needs a provider,
and what it writes to stdout.

### Verified, not asserted

- stitch without a transition: 3+3+2 → **8.00s exactly**
- stitch with a 0.8s dissolve: 3+3 → **5.20s**, because the clips genuinely overlap
- mid-transition frame **RGB(135,74,14)** between red (253,13,13) and green (17,136,16) —
  a hard cut would show one or the other
- every verb but `render` runs in a subprocess with **no ffmpeg on PATH and nine provider
  variables scrubbed**, including a test that asserts the scrubbed environment really is
  scrubbed
- conformance kit: **15 pass, 0 fail, 0 skip**

### Known limits

- `retime --ramp` is piecewise-constant. `atempo` takes a scalar and there is no
  time-varying tempo filter in ffmpeg, so a curve is approximated in subdivided steps.
- Transitions must be named presets, or described in a phrase for a model to pick from
  them. Generating a *new* transition is not built.
- `index`, `find` and `verify` do not exist yet.
- Nothing is a stream copy yet: every render re-encodes.
