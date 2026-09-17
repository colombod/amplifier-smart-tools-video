"""Per-verb documents: what `vid <verb> --help` prints.

Two readers, two documents. `-h` is the terse argparse summary a person scans to
remember a flag name. `--help` is an agent-facing document: what the verb is for,
what it costs, what it returns, and how to get to the next move. Both exist
because they answer different questions, and collapsing them serves neither.

Every document here states the same three things, because an agent that has to
infer them gets them wrong: whether the verb touches pixels, whether it needs a
provider, and what it writes to stdout.
"""

from __future__ import annotations

PIPE_RULE = """
## The pipe carries a plan, not pixels

Every verb except `render` reads a plan on stdin, appends one operation, and
writes the plan to stdout. Nothing decodes a frame until `render`, which compiles
the whole plan into ONE ffmpeg pass.

So this verb is instant, deterministic, and needs neither ffmpeg nor any AI
provider. It costs $0.00. Chain freely -- five operations cost one decode and one
encode, not five of each.

```bash
vid trim talk.mp4 --from 0:10 --to 2:30 \\
  | vid zoom --to 1.4 --at 0:45 \\
  | vid render out.mp4
```

Start a chain by naming a file. Continue one by piping. A verb given neither
fails and says so.
"""

_DOCS: dict[str, str] = {
    "trim": """# vid trim -- keep a time range

Discards everything outside `--from` .. `--to`. The commonest edit there is.

```bash
vid trim talk.mp4 --from 0:10 --to 2:30        # start a chain
vid trim --from 0:05 < plan.json               # continue one
```

Timecodes are written the way people write them: `90`, `1:30`, `1:02:03`,
`0:10.5`. Omit `--to` to run to the end.

**What it costs.** Nothing here. At `render` time a trim is the cheapest
operation there is -- and when the cut lands on a keyframe it can be a stream
copy with no re-encode at all. Cuts between keyframes cost one GOP.
""",
    "cut": """# vid cut -- remove a time range

The inverse of `trim`: keeps what surrounds the range and rejoins it.

```bash
vid cut talk.mp4 --from 1:12 --to 1:40         # drop the tangent
```

Both ends are required -- an open-ended cut is a trim, and this tool would
rather you say which you meant.

**What it costs.** The rejoin is a concat inside the same filter graph, so it is
still one encode at `render`. It is not a stream copy: the seam has to be built.
""",
    "retime": """# vid retime -- change speed, constantly or along a curve

```bash
vid retime --speed 2x                                  # twice as fast
vid retime --speed 0.5x                                # half speed
vid retime --ramp "1x@0 0.25x@1:05 1x@1:12"            # slow down and recover
```

**The ramp is the interesting one, and it is why this tool holds a plan at all.**
ffmpeg's `atempo` takes a SCALAR. There is no time-varying audio tempo filter and
no expression language for one, so a speed curve cannot be written as a filter.
It has to be decomposed into constant-speed regions, retimed separately, and
rejoined. That decomposition needs the shape of the whole edit, which is exactly
what the plan holds.

Audio follows video. Beyond 2x or below 0.5x, `atempo` is chained in stages --
each stage resamples, so extreme speeds cost a little quality.

**What it costs.** Nothing here. At render, a ramp becomes several trims and
several atempo chains in one graph -- larger than a constant speed, still one
encode.
""",
    "zoom": """# vid zoom -- animated zoom (Ken Burns)

```bash
vid zoom --to 1.4 --at 0:45 --duration 3
```

Zooms to `--to` over `--duration` seconds, centred on `--at`.

**The expertise this verb holds.** `zoompan` crops to whole source pixels, so a
slow zoom moves the crop by a fraction of a pixel per frame and the rounding
alternates 1,1,2,1,1,2 -- visible stutter. The fix is to upscale before zooming
so each rounding error is a fraction of an output pixel. This verb does that for
you; it is the reason a hand-written `zoompan` usually judders and this one does
not.

**What it costs.** Nothing here. At render it is genuinely expensive -- the
upscale is real work -- so keep `--duration` to the seconds that need it.
""",
    "stitch": """# vid stitch -- join clips, with or without a transition

```bash
vid stitch intro.mp4 talk.mp4 outro.mp4                       # hard cuts
vid stitch intro.mp4 - outro.mp4 --transition dissolve        # `-` is the plan on stdin
vid trim talk.mp4 --to 2:00 | vid stitch - outro.mp4 --transition fade --duration 0.8
```

`-` stands for the plan arriving on stdin, so you control where the running edit
sits in the order.

## Transitions, in three tiers

```bash
--transition dissolve                          a name. No model. $0.00.
--transition "soft and dreamy"                 a model picks one of 58.
--transition "slam in from the right,          no preset fits, so one is
              overshooting before it settles"  WRITTEN -- and proven.
```

**Tier 1** matches one of ffmpeg's 58 `xfade` presets. `vid transitions
--describe` lists them with what each looks like.

**Tier 2** hands a description to a model and asks it to pick from those 58. Its
answer is checkable against the list, so a wrong one is a loud error rather than
a plausible answer. Tier 2 always runs first — you never pay for tier 3 when a
preset would have done.

**Tier 3** only happens when the model reports that nothing in the 58 fits. It
writes a new `xfade` expression, and then **the tool renders it and measures it
before believing it.** A short probe of just the blend window is sampled: if the
middle frame does not sit between the two clips, the transition is refused and
you are told why, with the expression it tried.

This matters because a generated expression can compile perfectly and still move
wrongly. One in six did exactly that in testing — it read as obviously correct
and was wrong, because the expression runs per YUV plane rather than over RGB.
No model caught that. Three sampled frames did.

**A failed check refuses. It never falls back to `fade.`** Getting something
generic when you asked for something specific, and not being told, is the worst
outcome available.

Two things a caller usually finds out the hard way, which this verb handles:

- **`xfade` is video only.** Audio needs `acrossfade`, a different filter. Forget
  it and the picture blends over an audio track that hard-cuts.
- **A transition SHORTENS the result.** The clips overlap by `--duration`, so
  three 3-second clips joined with a 0.8s dissolve give 7.4 seconds, not 9.

**What it costs.** Joining without a transition can be a stream copy when the
inputs agree on codec and geometry. A transition cannot -- blending means
decoding both sides.
""",
    "caption": """# vid caption -- burn subtitles into the picture

```bash
vid caption --subtitles talk.srt
vid caption --subtitles talk.srt --style "FontSize=28,PrimaryColour=&H00FFFFFF"
```

Burned in, not a soft track: the words become pixels and survive any player,
upload or re-encode. That is usually what is wanted for social and demo video,
and it is irreversible, which is the tradeoff.

`--style` takes libass `force_style` syntax.

**What it costs.** Nothing here. At render the `subtitles` filter forces a
re-encode of the picture -- there is no way to burn text in without redrawing
frames.
""",
    "audio": """# vid audio -- the one place that touches sound on its own

```bash
vid audio remove  talk.mp4                              a silent video
vid audio replace talk.mp4 --with narration.wav         swap the track
vid audio mix     talk.mp4 --with music.mp3             lay a bed underneath
vid audio mix     talk.mp4 --with music.mp3 --level -12 louder bed
vid audio extract talk.mp4 out.wav                      pull the track out
```

**Everything else already carries audio for you.** `trim`, `cut`, `retime` and
`stitch` move the sound with the picture automatically -- a crossfade really does
run `acrossfade` under the blend, and a cut keeps both streams locked. These
verbs are for the cases where you want to change the sound *itself*.

## The length question, answered rather than inherited

For `replace` and `mix`, **the result is always the length of the video.** A
shorter track is padded with silence; a longer one is truncated. A long music
file cannot quietly extend your video.

## mix keeps your original audio where it is

`--level` applies to the **incoming** track only, in dB, and is negative in
normal use. Your existing audio is untouched, so you adjust one thing rather than
balancing two. The default `-18` puts music clearly under speech.

There is **no automatic ducking.** A flat level is predictable and easy to
explain; ducking is a real feature with its own decisions and should be asked for
on purpose rather than happening to you.

## The rest

`remove` on a video that has no audio is a no-op, not an error.

`mix` after `remove` in the same chain is refused -- there is nothing left to mix
into, and `replace` is what you want.

`extract` writes an audio file and does **not** produce a plan, so it ends a
chain rather than continuing one.

**What it costs.** $0.00, no provider, no network. All of this is ffmpeg.
""",
    "verify": """# vid verify -- check a rendered video against what you expected

```bash
vid verify out.mp4 --expect-duration 5.2
vid verify out.mp4 --expect-duration 5.2 --expect-resolution 640x360 --expect-audio
vid verify out.mp4 --expect-transition-at 2.6
vid verify out.mp4 --expect-no-black-frames
```

**Why this exists.** `render` produces a file and you have no way to know it is
right. A person opens it and looks. An agent editing unattended cannot -- so an
edit that renders successfully and is silently wrong is indistinguishable from a
correct one. This closes that gap.

Every check reports the **measured** value beside the **expected** one, and the
exit code is non-zero if any property is violated -- so it drops straight into a
shell chain:

```bash
vid trim in.mp4 --to 0:05 | vid render out.mp4 && vid verify out.mp4 --expect-duration 5.0
```

## The properties

- `--expect-duration N` with optional `--tolerance` (default 0.15s)
- `--expect-resolution WxH`
- `--expect-audio` -- present **and not silent**; a track of digital silence fails
- `--expect-transition-at T` -- a real blend, not a hard cut
- `--expect-no-black-frames` with optional `--longest-black`

## How the transition check works, since it is the interesting one

It samples three frames -- before, at, and after -- and asks whether the middle
one resembles **neither** side. A blended frame sits between the two; a cut puts
it on top of one of them.

The threshold is relative to how different the two sides are, so it also refuses
to claim a blend between clips that look nearly identical, where no measurement
could tell. A check that cannot decide says so rather than guessing.

**No model is involved in any of this.** These are measurements, not opinions.
Which is the point: it is the only kind of verification you can trust to grade
something a model produced.

**What it costs.** $0.00, and it needs no provider and no network. It does need
ffmpeg, because it is reading actual frames.
""",
    "index": """# vid index -- build a time-coded account of what is in a video

```bash
vid index talk.mp4                 # shots and speech
vid index talk.mp4 --no-speech     # shots only: free, no backend needed
```

Produces two things and stores them where they persist:

- **shots** -- boundaries from ffmpeg scene detection. Deterministic, free, no
  provider. This is the cheapest useful work in the tool: it takes the visual
  problem from eighteen thousand frames down to a few dozen, one per shot.
- **speech** -- a transcript in time-coded passages, produced locally.

**The index is the expensive artifact, so it is built once and reused.** Ten
questions about one video cost one transcription. It is keyed by the video's
CONTENT, not its path -- a renamed file is the same video, a re-export is not.

**What it costs.** Shots are free. Speech needs a backend:

```bash
uv tool install --force 'vid[speech] @ git+https://github.com/colombod/amplifier-smart-tools-video'
```

Nothing is uploaded. Transcription runs on your machine.
""",
    "find": """# vid find -- locate a moment by what was said

```bash
vid find "pricing" talk.mp4                        # a plan, ready to pipe
vid find "pricing" talk.mp4 --show                 # the hits, with their evidence
vid find "pricing" talk.mp4 | vid render clip.mp4  # cut straight to it
```

Searches the index and returns a **time range grounded in the transcript**, with
the passage that produced it. By default it writes a plan trimmed to that range,
so it composes with everything else.

## Two tiers, same as the rest of the tool

- **literal** -- the words appear in the transcript. No model, no provider,
  $0.00. Tried first, so a caller who knows what was said never pays for a model.
- **described** -- a phrase a model matches to passages. Needs a provider.

A literal hit must carry **at least half** your query's meaningful words. Below
that it escalates rather than answering: matching one incidental word and
returning a confident range is worse than admitting the words do not appear.

## The guard

**A model is never asked for a timestamp.** The index holds passages that already
carry times the transcriber produced. A model is shown those passages and asked
*which ones, by id*; the time is then a lookup.

This is not a discipline, it is a shape. A model cannot return a wrong time
because it is never in a position to return a time at all -- the worst it can do
is name a passage that does not exist, and that is a membership test away from
being caught. A timestamp invented by a model is indistinguishable from a correct
one until somebody watches the video, which is exactly why it is never asked for.

**Every hit carries its evidence.** A range with no text beside it is a claim you
cannot check.
""",
    "render": """# vid render -- compile the plan and encode, once

```bash
vid render out.mp4                      # do it
vid render out.mp4 --print-command      # show the ffmpeg it would run, and stop
```

**This is the only verb that touches pixels.** It takes the whole plan, compiles
it into a single `filter_complex`, and runs one ffmpeg. A chain of five
operations costs one decode and one encode -- not five of each, which is what
naively shelling out per operation would cost.

**`--print-command` is the honest window.** It prints the exact invocation and
runs nothing. Use it to check what an edit will actually do, to learn the ffmpeg
behind a verb, or to hand the command to something else entirely. A tool you
cannot see through is a tool you cannot debug.

**What it needs.** ffmpeg on PATH -- and only here. Every other verb runs
without it. `--print-command` needs nothing at all.
""",
    "plan": """# vid plan -- show the edit, without performing it

```bash
vid trim talk.mp4 --from 0:10 | vid plan             # read it
vid trim talk.mp4 --from 0:10 | vid plan > edit.json # keep it
vid plan < edit.json | vid render out.mp4            # replay it
```

A plan is JSON. It can be saved, diffed, reviewed, hand-edited and replayed --
which is what makes an edit proposed by a model as inspectable as one typed by a
person. The model writes a plan; you read it before a frame is touched.

**What it costs.** Nothing, and it needs nothing.
""",
    "transitions": """# vid transitions -- the 58 ways two clips can meet

```bash
vid transitions              names only, one line -- cheap to scan
vid transitions --describe   each one with what it actually looks like
```

**Read this before describing a transition in words.** If one of these is what
you want, naming it costs nothing: no model, no provider, no network. Describing
it instead spends a model call to arrive at the same answer.

The list is ffmpeg's own `xfade` set, so every name here is guaranteed to work.
A name that is not on this list is refused rather than approximated.

## How these relate to the three tiers

- name one of these           -> tier 1, deterministic, $0.00
- describe it in a phrase     -> tier 2, a model picks from this list
- describe something not here -> tier 3, one is written, then proven

`vid stitch --help` explains the tiers in full, including what happens when a
generated transition fails its check.

**What it costs.** $0.00. This reads a list in the tool; it does not even touch
ffmpeg.
""",
    "manifest": """# vid manifest -- what this tool tells a program about itself

```bash
vid manifest              the machine-readable descriptor, as JSON
vid manifest | jq .       if you want to read it yourself
```

Prints the Smart Tool manifest: the tool's name, version, what it can do, and
what it needs installed to do it. Written for a catalog, a host, or an agent
deciding whether this tool is worth calling -- not primarily for a person.

## What you will find in it

**`capabilities`** -- every verb, with whether it is model-backed. Note that
`find` is *not* marked model-backed: its literal search answers most queries with
no provider at all, and marking it otherwise would tell you that you need
credentials you do not need.

**`requires`** -- the dependencies, with `optional` stating whether the tool
works without each one:

| | |
|---|---|
| `ffmpeg` | **required.** `render`, `verify` and `index` cannot work without it. Everything that only builds a plan works fine. |
| `faster-whisper` | optional. Transcription for `index` and `find`. Runs locally; nothing is uploaded. |
| `gh` + Copilot | optional. Only for *describing* a transition or a moment in words instead of naming it. |

**For what is actually installed on THIS machine right now, run `vid check`.**
The manifest says what the tool can need; `check` says what you have.

**What it costs.** $0.00, no provider, no network, and it does not touch ffmpeg.
""",
    "check": """# vid check -- what this installation can actually do

```bash
vid check
```

Reports the tier you are in: which backends are present, which are missing, and
what each missing one would unlock -- with the exact command that would install
it.

**Why this verb exists.** Most of this tool works with nothing but ffmpeg.
Some of it needs a speech backend; some needs a vision one. Rather than
discovering that through a failure three verbs into a chain, ask.

**What it costs.** Nothing, and it needs nothing. It is a report on your machine,
not a request to anyone.
""",
}


def verb_doc(name: str) -> str:
    """The document for one verb, with the pipe rule appended where it applies."""
    body = _DOCS.get(name)
    if body is None:
        raise KeyError(name)
    if name in {"render", "plan", "check"}:
        return body.strip() + "\n"
    return body.strip() + "\n" + PIPE_RULE


def verbs() -> tuple[str, ...]:
    return tuple(_DOCS)
