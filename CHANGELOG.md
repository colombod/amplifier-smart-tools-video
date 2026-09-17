# Changelog

Notable changes to `vid`, newest first. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are semantic.

**Why this file exists from the first release.** A sibling repository shipped fifteen
commits under one version number — including a fix to cost reporting that had been wrong on
every run it ever performed — because nothing forced the question "is this shipped?". A
version is a claim about what someone installed. This file is where that claim is kept
honest.

## 0.1.0 — unreleased

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
