# Architecture

Mechanics. For *why this exists*, read `VISION.md`.

## The one decision everything follows from

**The pipe carries a plan, not pixels.**

```bash
vid trim talk.mp4 --from 0:10 --to 2:30 \
  | vid retime --ramp "1x@0 0.25x@1:05 1x@1:12" \
  | vid zoom --to 1.4 --at 0:45 \
  | vid stitch intro.mp4 - outro.mp4 --transition dissolve:0.8 \
  | vid render out.mp4
```

Every verb reads a plan on stdin, appends one operation, and writes the plan to stdout.
Nothing decodes a frame until `render`, which compiles the whole plan into **one**
`filter_complex` and encodes **once**.

Why this and not the obvious thing:

- The obvious thing — each verb shells out to ffmpeg and writes a temp file — costs N
  decodes and N encodes for N operations, and loses quality at every step. Measured
  elsewhere at 2–10× slower than a single graph for the same work.
- Every serious tool converges here. auto-editor, ffmpeg-python, MoviePy and LosslessCut
  all accumulate operations and compile one graph.
- It falls out of a constraint we cannot avoid anyway: `atempo` takes a scalar, so a
  non-linear speed ramp **must** be decomposed into constant-speed regions before it can
  be expressed. Once the tool holds the shape of the edit to do that, holding it for
  everything else is free.

Consequences worth stating plainly:

- **Every verb except `render` is instant, deterministic, and needs no ffmpeg and no
  provider.** A plan is JSON. The spec's "deterministic paths run with no AI configured"
  is not something we work around; it is the default state of the tool.
- **The plan is the artifact.** It can be saved, diffed, reviewed, hand-edited, and
  replayed. An edit produced by a model is as inspectable as one typed by a person.
- **`render` can explain itself.** `--print-command` emits the ffmpeg invocation. A tool
  you cannot see through is a tool you cannot debug.

## Layer one: the mechanical verbs

Deterministic. No model. No credentials.

| verb | what it does | the expertise it holds |
|---|---|---|
| `trim` | keep a time range | keyframe alignment; when a cut is free and when it costs a GOP re-encode |
| `cut` | remove a time range | the same, inverted |
| `retime` | constant speed, or a ramp | `setpts` for video, `atempo` for audio — and that `atempo` is scalar-only, so a ramp is segmented into constant regions and rejoined |
| `zoom` | Ken Burns / animated zoom | `zoompan` quantises the crop to whole source pixels, so a slow zoom stutters; upscale first |
| `stitch` | concatenate | concat demuxer (lossless, identical params) vs concat filter (re-encodes); which applies |
| `transition` | between two clips | `xfade` is video-only and always re-encodes; audio needs `acrossfade`; offsets are cumulative |
| `caption` | burn in subtitles | the `subtitles` filter over an SRT the speech index already produces |
| `render` | compile and encode | the whole graph, once |
| `plan` | show / validate | — |

**Lossless where possible, honestly.** `trim` and `stitch` can be stream copies when the
cut lands on a keyframe and the inputs agree. When they cannot, the tool says so rather
than silently re-encoding: a caller who asked for a fast cut deserves to know it was not
one.

## Layer two: the index

Answering *"where does she explain pricing?"* requires a time-indexed account of the
video's content. That index is the expensive artifact, and it is built once and reused.

```
vid index <video>
  shots     ffmpeg scene detection            deterministic, free, no provider
  speech    transcript, WORD-level timing     local (whisper.cpp) or API
  vision    one description per shot          vision provider, only when asked
```

Three things make this shape right:

- **Shot detection is free and deterministic.** It needs no model at all, and it reduces
  the visual problem from 18,000 frames to ~40 — one per shot. Describing 40 frames costs
  cents; describing 600 does not.
- **Word-level timestamps are table stakes.** Segment-level timing is too coarse for a
  frame-accurate cut. Note that OpenAI's newer `gpt-4o-transcribe` **dropped** word-level
  granularity that `whisper-1` has — a regression worth knowing before choosing a backend.
- **The index is durable and shared.** Like a research run directory, it accumulates.
  Ten questions about one video cost one transcription.

## Layer three: the smart verbs, and the guard that makes them trustworthy

```bash
vid find "where she explains pricing" talk.mp4 | vid trim | vid render out.mp4
```

`find` returns time ranges **grounded in the index**, with the evidence that produced them.

**A model is never asked for a timestamp.** This is not a stylistic preference. LLMs
fabricate timecodes — plausible, confident and wrong — and a fabricated timestamp is
indistinguishable from a correct one until you watch the output. So:

1. The index produces chunks, each already carrying a real time range from the transcriber.
2. The model is shown the chunks and asked **which ones**, by id.
3. The timestamp comes from the index, by lookup.

The model's judgment is used for the thing it is good at — *"is this passage about
pricing?"* — and is structurally prevented from touching the thing it is bad at. A
returned chunk id that does not exist is a loud error, not a plausible wrong answer.

That guard is testable, and it is the first thing that should have a test.

## What is parked, and why it is written down

**Remotion** — a React frame-synthesis engine — was assessed as a rendering backend for
the visually authored parts: captions, shapes, annotations, richer transitions. It is
genuinely strong at those. It is parked, for reasons that are worth keeping:

- It has **no expression for passing bytes through**. Its only `-c:v copy` refers to its
  own pre-encoded chunks, never to source video. Every output frame is synthesised
  through a headless-browser screenshot and re-encoded. Most of this tool's verbs are
  exactly the operations it cannot do.
- Its licence requires payment above three employees and restricts derivative works —
  an obligation a published tool would pass to every user.
- Its captions package is data-only (parse/serialise SRT); there is no renderer. Its
  speed-ramp recipe is O(n²). There is no Ken Burns package.

None of that makes it a bad library; it makes it the wrong *core* dependency for a tool
whose main job is not touching pixels. If it returns, it returns as an optional adapter
over short segments, behind the same plan.

## Open questions

- **Backend choice for speech** is a real decision with a cost/latency/offline tradeoff,
  and it is a per-caller decision rather than one we should make for everyone.
- **Which provider capabilities to declare.** This tool needs up to three distinct ones —
  speech-to-text, vision, and text reasoning — where our research tools needed one. The
  spec's manifest has no vocabulary for that yet, and this is the clearest evidence we
  have that it should.
