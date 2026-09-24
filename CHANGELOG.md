# Changelog

Notable changes to `vid`, newest first. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are semantic.

**Why this file exists from the first release.** A sibling repository shipped fifteen
commits under one version number — including a fix to cost reporting that had been wrong on
every run it ever performed — because nothing forced the question "is this shipped?". A
version is a claim about what someone installed. This file is where that claim is kept
honest.

## Unreleased

`stitch` no longer emits a broken audio branch. It interpolated the running audio label
into its `concat` entry without the `is not None` guard the rest of the compiler applies,
so a silent source or a prior `audio remove` put the literal text `None` into the filter
graph and ffmpeg rejected the whole command. `cut` already guarded this; `stitch` and the
transition path now do too.

It also assumed every stitched clip carried sound. Audio presence was probed on the plan's
source alone, so stitching a silent clip emitted a stream specifier matching nothing. The
render path now probes each stitch source, and a join where exactly one side has sound is
refused by name rather than failing as `Stream specifier ... matches no streams`.

Deliberate silence is distinguished from incidental silence: after `audio remove` the
caller has already said what to do with sound, so dropping an incoming clip's audio carries
out that instruction and is not refused. `vid stitch --help` documents the new refusal.

Both faults were silent until render -- the plan validated and the compile succeeded.

`stitch` can now join clips that are not the same size, which it previously could not do
at all: it applied no normalization, and `concat` requires matching resolution and SAR, so
a mixed-size join was rejected by ffmpeg rather than caught by `vid`. The reported case
mixed 1280x720 animation with 1920x1080 recordings.

`--fit fit` preserves aspect and pads the remainder with bars, so nothing leaves frame.
`--fit fill` preserves aspect and crops the overflow centred, so nothing is letterboxed.
Neither stretches. A size mismatch with no mode stated is refused, naming both sizes and
both modes; there is deliberately no default, because resizing footage without being asked
changes the framing without saying so.

`fit` is a new optional field on the `stitch` operation, so `plan_format` stays `1`.

## 0.3.3

0.3.2's zoom fix failed on stable ffmpeg 6.1.1 (exit 234: `scale2ref`'s reference variables
could not parse in the complex segmented graph), while the upstream build tolerated it. Replaced
rather than patched.

The zoom ramp is now compiled to ONE `zoompan` filter node instead of a 52-node graph with
trim, split, crop, scale2ref, nullsink, setsar, and concat. `zoompan` recomputes its `zoom`
expression every input frame, so there is no ramp to segment and nothing to reconcile across
clips of different sizes. The problems 0.3.2 tried to solve were not defects in the workaround's
approach — they were misunderstandings of how `zoompan` worked: its `d` is output **frames
per input frame** (not duration), and without an explicit `s=` it silently defaults to
`hd720` regardless of the source. Both are parameters, not workarounds. A 640x360 clip now
stays 640x360; an 8-second clip now renders as 8 seconds, not 400.

### Fixed

- **`zoom`'s filter graph shape incompatible with stable ffmpeg 6.1.1.** Rewritten to use
  `zoompan` single-node directly, eliminating the concat of scaled segments that refused
  to parse.

## 0.3.2

Spec compliance and three rendering bugs fixed. The tool was run through
`smart-tool-creator check-spec-adherence` for the first time; it scored 7 adhere / 10 deviate
while passing the deterministic conformance kit 16/16. Closing those deviations while
actually rendering video to verify results uncovered three real bugs that no test, kit or
reviewer had caught.

### Fixed

- **`zoom` rendered 50x longer than requested.** ffmpeg's `zoompan` takes output **frames per
  input frame**, not the effect's total length. The code set it to `duration * fps`, so each
  input frame fanned out into 60 output frames. `--at` was also ignored — the ramp ran
  across the whole clip instead of starting at the specified time. Result: an 8-second clip
  rendered as 400 seconds. Rewritten as segmented crop+scale2ref ramps; duration and
  resolution now hold exactly.

- **Composed audio chains broke for 10 of 12 audio-touching combinations.** A chain like
  `trim | audio remove | render` failed with "Filter 'asetpts:default' has output 1 (a2)
  unconnected". The compiler emitted an audio branch, a later `audio remove`/`audio replace`
  dropped the reference, and the dead pad made ffmpeg refuse the whole graph. Fixed with a
  post-pass that seals dangling outputs to nullsink/anullsink. A second defect surfaced
  underneath: `trim`/`cut` never updated `elapsed`, so `audio replace`/`audio mix` after
  them had no duration bound and rendered hours of silence instead of failing.

- **Scene detection never checked ffmpeg's exit code.** A failed detection silently persisted
  an index claiming one shot spanning the whole video, indistinguishable from a real
  single-shot result. Shot detection is foundational to both `narrate` (slots) and
  `index --vision` (what frames to describe), so undetected failures cascaded downstream.
  Now refuses to fall back — an index with a false single-shot claim is worse than no index.

- **`ty check` (the Typer type checker) was enabled as a declared hook but had ~50 failures,
  was absent from CI, and contributors learned to ignore it.** Fixed the genuine issues
  (dispatch-dict narrowing, tuple shapes, piper kwargs unpacking, Typer overloads) and added
  `ty check` to CI so it cannot rot again.

### Changed

- **Library now exports `audio_remove`, `audio_replace`, and `audio_mix`.** These were
  available only through the CLI before.
- **All error messages from external commands now include remediation hints.** ffmpeg/ffprobe
  failures name what to check and point to `vid check`.
- **Rendered skill now documents its actual prerequisites.** The manifest was correct; the
  skill claimed only `uv` was needed.
- **Help text introspection tests added.** A test that reads the real Click command tree and
  fails if any flag is undocumented prevents drift between `--help` output and documented
  arguments.
- **Composed-chain render tests added.** Each of the 12 audio-touching combinations now has
  a test that renders to a file and measures duration with ffprobe.

## 0.3.0

Four capabilities, and four defects that only measurement found.

### Added

- **`narrate`** — write a narration from a prompt, speak it locally, and fit it to
  the video's own timing. The shot boundaries become slots with durations; the
  model writes one line per slot against its budget; every line is spoken and
  **measured**; an overrun is rewritten, then allowed a modest speed-up, then
  **reported by name** rather than quietly overrunning. One unfittable line is
  named and the rest still produce a usable result. `--script-only` prints the
  script and synthesises nothing.
- **`index --vision`** — describe one frame per shot, so a silent screen
  recording stops being invisible to `find`. Shot detection is what makes it
  affordable: a few dozen frames instead of eighteen thousand. The cost is stated
  before it is spent, and refused rather than guessed at when nobody can answer.
  No timestamp ever comes from a model.
- **`recolor --like <image>`** — take the palette from a reference picture and map
  the video onto it. Reinhard transfer in Lab, baked into a lookup table applied
  in the same single pass as everything else. **Needs no provider, no network and
  no image library** — a `.cube` table is plain text.
- **`vignette`, `grade`, `lut`** — how the picture *looks*. Seven named looks,
  because `--look warm` is one decision a caller can make and
  `--warmth 0.3 --contrast 1.1` is four they have no basis for.

### Fixed

- **Any video without an audio track failed at render** — `-map 0:a` on a stream
  that does not exist, reported by ffmpeg as `Stream map '' matches no streams`.
  Screen recordings routinely have no audio and are the commonest thing this tool
  is pointed at, so **every verb** was broken for them, and no test caught it
  because every fixture happened to have sound.
- **Shot detection missed real cuts.** The threshold was `0.4`; genuine hard cuts
  score as low as `0.076`, so a three-shot video reported one shot. Load-bearing
  twice over — it underpins both the vision story and narration's slots.
- **The named looks were invisible.** Colour weights that read as reasonable
  measured a Lab b* shift of `+0.23` — the right direction and no use to anyone.
- **Every `--help` now returns a document.** `transitions` and `manifest` had no
  entry at all, and `audio` fell through to a usage box. That matters for an agent,
  which asks every verb the same question and got a different *kind* of answer
  from three of them.

### Extras

`[voice]` adds local speech synthesis, `[all]` installs everything. Verified in a
clean container: `piper-tts` rides alongside `faster-whisper` with **zero version
changes** to anything already installed.

### Verified

103 tests. Conformance 15/15 locally, 16/16 against the tool installed from its git
URL in a container that had never seen the checkout.

Measured rather than asserted: a vignette darkens corners **37.3%** against an
identical-to-begin-with source; warm and cool move Lab b* **+2.49** and **-3.30**;
colour transfer closes **98.4%** of the distance to its reference; a narration line
lands **4.25s into a 6.00s** slot.

### Known limits

- ~~`retime 2x` on a 3.0s clip produces 1.57s rather than 1.50s~~ — **fixed after
  0.3.0 was tagged.** Two stacked errors: `setpts` left frames off the uniform
  grid, and underneath that `atempo`'s rounding runs long when speeding up and
  **short** when slowing down. Every speed now lands exactly.
- Palette *quantisation* (the poster look) is designed but not built; `recolor`
  does the grade.
- `blur --region`, for obscuring a token in a demo recording, does not exist yet.

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
