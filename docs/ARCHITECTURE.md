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
- **Segment-level timing is enough, and claiming otherwise was wrong.** An earlier draft
  of this document called word-level timestamps "table stakes". Checking the backends
  refuted it twice over. First, almost nothing produces true word-level timing: whisper.cpp
  and faster-whisper both emit segment boundaries, and only whisperX does real per-word
  alignment, at roughly 600 MB of extra dependencies and a slower pass. Second, and more
  decisively, **the precision chain has a weaker link than the transcript.** A lossless cut
  lands on a keyframe, and a GOP is commonly two seconds — so sub-second word precision is
  discarded by the very next constraint. Segments are typically 2–10 seconds, which is the
  right granularity for *finding a region*, which is the actual use case.

  Word-level remains a real upgrade for karaoke-style captions and for
  cut-exactly-at-the-sentence on a re-encode. It is an opt-in backend, not a floor.
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

## Backends, and the install cost of each

**Local inference is the default.** Transcription is the one capability here that has a
genuinely good offline story, and a caller should not have to send a video's audio to a
third party to find out when someone said "pricing".

But local inference is not free — it is paid in install complexity instead of dollars, and
that cost lands on whoever has to set the tool up. Increasingly that is an agent, running
unattended, which cannot answer a prompt or debug a compiler error.

| tier | needs | unlocks | real cost |
|---|---|---|---|
| **0** | ffmpeg | every mechanical verb | one system package |
| **1** | + `faster-whisper` | `index speech`, `find` | one `pip install`, no compiler; model auto-fetched and cached (~140 MB for `base.en`) |
| **1′** | + whisper.cpp | same, faster on Apple Silicon | `brew install whisper-cpp` on macOS; on Linux there is **no apt package** and it is a cmake build |
| **2** | + an API key | cloud transcription | no install at all, but money and a network round trip |
| **3** | + whisperX | true per-word timing | ~600 MB and a slower pass |

**Default: faster-whisper.** Not because it is the best transcriber — whisper.cpp with
Metal is faster on a Mac — but because it is the only one an agent can install unattended
and expect to succeed: a single `pip install`, no compiler, no platform branch, idempotent
model download, works CPU-only in a container.

Two traps worth recording, since both would be discovered the hard way:

- whisper.cpp's binary was **renamed from `main` to `whisper-cli`**. Anything shelling out
  to it must not assume the old name.
- A prebuilt whisper.cpp binary can die with SIGILL on a machine lacking the build host's
  CPU features. Building portable requires `-DGGML_NATIVE=OFF`.

**Gemini is for embeddings, not for timecodes.** Its embedding models are cheap and good,
and semantic search over transcript chunks is exactly the job. Asking it — or any model —
to *return a timestamp* runs straight into the failure this design exists to prevent.

## Setup is a first-class feature, not a README section

This is the first tool here where getting to a working state is genuinely hard, and it has
to be treated as part of the product:

- **`check` reports the tier you are actually in** — which backends are present, which are
  missing, and what each would unlock. Deterministic, free, no credentials.
- **Every refusal names the remedy.** A verb that needs a backend you do not have fails
  with the exact command that would fix it, not a stack trace.
- **The skill carries the ladder.** A harness installing this tool reads one document and
  can get from nothing to working without a human.

## Open questions

- **The manifest has no vocabulary for tiers or alternatives.** `requires[]` is a flat
  list, and this tool has four alternative paths to one capability, each with a different
  install cost, plus capabilities that are genuinely optional. Our research tools needed
  one backend and did not expose this. This is the clearest evidence we have that the
  spec's `requires[]` needs to express *alternatives* and *what each unlocks*.
- **Three distinct provider capabilities** — speech-to-text, vision, text reasoning —
  where our research tools needed one. "Which AI providers does this tool use?" has a
  compound answer here, and the manifest cannot currently give it.
