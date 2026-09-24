# The edit plan, v1

**What this is.** The promise `vid` makes about the JSON that travels between its verbs.
Everything here may be relied on by other programs; anything not written here may change
without notice.

This is the one contract worth pinning today, and the reason is narrow: the plan is the
only thing that crosses a boundary. Another tool will generate one, a person will
hand-edit one, and `render` has to accept both. The CLI surface is still moving — `find`
and `index` do not exist yet — and a contract written over moving parts either blocks the
work or gets quietly ignored.

---

## The shape

```json
{
  "plan_format": 1,
  "source": "talk.mp4",
  "operations": [
    { "op": "trim", "start": 10.0, "end": 150.0 }
  ]
}
```

- **`plan_format`** — an integer. This document describes `1`.
- **`source`** — the video the edit starts from. A path, as the caller wrote it.
- **`operations`** — applied **in array order**. An empty array is a valid plan that
  re-encodes the source unchanged.

**Every time is in seconds, as a number.** `1:30` is a convenience the CLI accepts from a
person; it never appears in a plan. A program writing a plan writes `90.0`.

---

## The operations

Each carries an `op` discriminator. Unknown fields are **rejected**, not ignored — a
misspelled key is a mistake worth hearing about, and silently dropping it would produce an
edit that is subtly not the one asked for.

| `op` | fields | meaning |
|---|---|---|
| `trim` | `start`, `end` (nullable) | keep this range; `end: null` runs to the end |
| `cut` | `start`, `end` | remove this range, rejoin what surrounds it |
| `retime` | `speed` **or** `ramp[]`, `pitch` | constant speed, or a curve of `{at, speed}` points |
| `zoom` | `to`, `at` (nullable), `duration`, `x`, `y` | animated zoom; `x`/`y` are ffmpeg expressions |
| `stitch` | `sources[]`, `fit`, `transition`, `transition_duration`, `transition_offset`, `transition_requested`, `transition_rationale` | append clips, optionally blending |
| `overlay` | `source`, `x`, `y`, `width` (nullable), `height` (nullable), `start` (nullable), `end` (nullable), `mask` (nullable), `motion` (nullable), `audio` (nullable), `key` (nullable), `opacity` | lay another clip over the picture |
| `caption` | `subtitles`, `style` (nullable) | burn in a subtitle file |

**`retime` takes exactly one of `speed` or `ramp`.** Both, or neither, is invalid.

**`overlay` takes `width` and `height` together or not at all.** One alone is invalid: the
other would have to be derived from an aspect ratio the caller never stated. Its geometry
is in PIXELS of the edit's own frame, and it never contributes audio.

**`overlay.mask` is an object or absent.** Absent is the whole rectangle. Its `kind` is
one of `rect`, `rounded_rect`, `circle`, `ellipse`, `image` or `video`; `image` and
`video` additionally require `source`, the file the matte is read from. `radius` applies
only to `rounded_rect`, `invert` swaps keep for drop, and `feather` softens the edge in
pixels. The mask is resolved at the layer's own size, before any resize, so it is
described relative to the layer and survives the layer being scaled.

**`overlay.motion` is an object or absent.** Absent holds the stated geometry throughout.
Its `to_x`, `to_y`, `to_width` and `to_height` are where the layer ends, the overlay's own
`x`/`y`/`width`/`height` being where it starts; `start` and `duration` are the window, in
seconds, and `easing` is `linear` or `ease_in_out`. `to_width` and `to_height` are given
together or not at all. **Motion animates presentation only and never retimes the layer**,
so the layer's own source timing is unaffected by it.

**`overlay.audio` is an object or absent, and absent means `drop`.** Its `policy` is
`drop`, `keep` or `only`; `gain_db` and `base_gain_db` are in dB; `duck` enables a
sidechain compressor whose `duck_threshold`, `duck_ratio`, `duck_attack` and
`duck_release` are recorded on the object rather than hidden in the implementation, so a
plan states what it actually did. **Mixing sums rather than averages**: `amix` runs with
`normalize=0`, because scaling every input by 1/n changes the base's level for no reason
the caller asked for.

**`overlay.key` is an object or absent, and `overlay.opacity` runs 0..1 with 1 the
default.** A key's `kind` is `colorkey`, `chromakey` or `lumakey`; `colour` applies to the
two colour keys and `threshold` to `lumakey`, with `similarity` and `blend` shaping the
edge. **A key and a mask INTERSECT** -- a key reads the layer's own content, a mask
imposes a shape from outside, and both must pass for a pixel to survive.

**The composition order is part of the contract**: key, then mask, then feather, then
opacity. It is observable in the output, so it is promised rather than left to the
implementation.

**In `stitch.sources`, the string `"-"` means "the plan built so far"**, and it holds a
position: `["intro.mp4", "-", "outro.mp4"]` puts the running edit in the middle.

**`stitch.fit` is `"fit"`, `"fill"`, or absent.** It decides how a clip that is not the
edit's own size is resolved: `fit` preserves aspect and pads the remainder, `fill`
preserves aspect and crops the overflow centred. Neither stretches. Absent means the
caller has not chosen, and a size mismatch is then **refused** rather than normalised —
resizing footage without being asked changes the framing without saying so. A plan whose
clips are all one size never consults it.

---

## What a caller may rely on

**A plan is inert.** Reading, writing, validating or transforming one touches no video,
runs no ffmpeg, and calls no model. Every verb that builds a plan works with no ffmpeg
installed and no credentials configured.

**A model never runs at render time.** If a model contributed to a plan, it ran when the
plan was built and its output is already resolved into the fields. So:

- the same plan renders identically on a machine with no credentials at all
- re-rendering never re-invokes a model — no cost drift, no nondeterminism
- what a model chose, and why, is readable before a frame moves

**Provenance is explicit.** Where a value could have come from a model, two companion
fields say whether it did:

| field | when a caller named the value | when a model chose it |
|---|---|---|
| `transition` | the preset, e.g. `"dissolve"` | the preset the model picked |
| `transition_requested` | `null` | what the caller actually asked for |
| `transition_rationale` | `null` | one line on why this preset |

`transition_requested === null` means **no model was involved in this operation.** That is
a promise, not a convention.

**Order is preserved and meaningful.** Operations apply in sequence; the plan is not a set.

**Plans are additive.** A verb appends one operation and never rewrites an earlier one.

---

## What is NOT promised

- **That an operation list is minimal.** Two adjacent trims stay two operations. Nothing
  optimises a plan, and a future version may.
- **The compiled ffmpeg command.** `render --print-command` prints what would run today.
  Filter-graph construction is an implementation detail and will change.
- **Intermediate label names** inside the filter graph (`v1`, `a2`, …). Read the plan, not
  the graph.
- **That every operation can be a stream copy.** Whether a cut lands on a keyframe depends
  on the file, not the plan.
- **`transition_offset`.** It is derived on the render path by probing durations, and a
  plan written by hand may leave it `0.0`. Do not compute it yourself; `render` will.

---

## Changing this

`plan_format` is a single integer and it means compatibility, not recency.

- **Adding an optional field, or a new `op`,** keeps format `1`. Older `vid` versions
  reject the unknown key loudly rather than mis-rendering it, which is the behaviour we
  want.
- **Changing the meaning of an existing field, or removing one,** requires `2`.

A plan whose `plan_format` this tool does not recognise is **refused with its number
named**. A plan silently rendered under the wrong rules would produce a video that is
plausibly wrong, which is the failure mode this whole design exists to avoid.
