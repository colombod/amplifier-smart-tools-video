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

**Transitions.** `--transition` names one of ffmpeg's `xfade` presets: `fade`,
`dissolve`, `wipeleft`, `wiperight`, `slideup`, `slidedown`, `circleopen`,
`circleclose`, `pixelize`, `zoomin`, and around thirty more.

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
