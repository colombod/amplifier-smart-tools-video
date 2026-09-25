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

**Arguments.**
- `source` (optional) -- a file to start a chain, or omit to continue a piped plan.
- `--from` (optional, default `0`) -- where to start keeping.
- `--to` (optional, default none) -- where to stop. Omit to run to the end.

**Result.** A plan with one more operation, `trim`, written to stdout.

**Failures.** A `--from`/`--to` that is not a timecode (not `90`, `1:30`,
`1:02:03` or `0:10.5`, and not negative): refused, naming what was found.
Neither a file argument nor a plan on stdin: refused, naming the fix.

**What it costs.** Nothing here. At `render` time a trim re-encodes the picture.
Picture stream-copy is only supported for audio-only plans, not trims.
""",
    "cut": """# vid cut -- remove a time range

The inverse of `trim`: keeps what surrounds the range and rejoins it.

```bash
vid cut talk.mp4 --from 1:12 --to 1:40         # drop the tangent
```

Both ends are required -- an open-ended cut is a trim, and this tool would
rather you say which you meant.

**Arguments.**
- `source` (optional) -- a file to start a chain, or omit to continue a piped plan.
- `--from` (required) -- where the removed range begins.
- `--to` (required) -- where it ends.

**Result.** A plan with one more operation, `cut`, written to stdout.

**Failures.** `--from` or `--to` omitted: Typer refuses before this tool sees
the call (exit 2). Either one not a timecode: refused, naming what was found.
Neither a file argument nor a plan on stdin: refused, naming the fix.

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

**Arguments.**
- `source` (optional) -- a file to start a chain, or omit to continue a piped plan.
- `--speed` (optional, default none) -- a constant speed: `2x`, `0.5x`. Exactly
  one of `--speed`/`--ramp` is required.
- `--ramp` (optional, default none) -- a curve: `"1x@0 0.25x@1:05 1x@1:12"`,
  each point written `speed@time`.

**Result.** A plan with one more operation, `retime`, written to stdout.

**Failures.** Neither or both of `--speed`/`--ramp` given: refused, naming the
fix. A `--speed` that is not a positive number (`2x`, `0.5x`, or bare): refused.
A `--ramp` token with no `@`: refused, naming the token. A ramp of fewer than
two points is accepted here and refused later, at `render`.

**What it costs.** Nothing here. At render, a ramp becomes several trims and
several atempo chains in one graph -- larger than a constant speed, still one
encode.
""",
    "zoom": """# vid zoom -- animated zoom (Ken Burns)

```bash
vid zoom --to 1.4 --at 0:45 --duration 3
```

Zooms to `--to` over `--duration` seconds, centred on `--at`.

**The expertise this verb holds.** `zoompan`'s own `d` option is output frames
PER INPUT FRAME CONSUMED, not the effect's length, and with no explicit `s=` its
output silently defaults to `hd720` regardless of the source. Get either wrong
and the render either runs to some multiple of the source's length or comes out
the wrong size. This verb pins both -- `d=1` and the source's own dimensions --
and gates the zoom expression to the `--at`/`--duration` window itself, which is
the reason a hand-written `zoompan` call usually needs both bugs found the hard
way and this one does not.

**Arguments.**
- `source` (optional) -- a file to start a chain, or omit to continue a piped plan.
- `--to` (optional, default `1.3`) -- final zoom factor. `1.4` is a noticeable push.
- `--at` (optional, default none) -- centre the zoom on this moment.
- `--duration` (optional, default `3.0`) -- seconds the zoom takes.

**Result.** A plan with one more operation, `zoom`, written to stdout.

**Failures.** `--at` that is not a timecode: refused, naming what was found.
Neither a file argument nor a plan on stdin: refused, naming the fix.

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
- **Reserve handles on BOTH sides.** The outgoing clip's last `--duration`
  seconds and the incoming clip's first `--duration` seconds are blended,
  including their sound. Keep narration outside those handles. For a 0.8s
  dissolve, place incoming narration with `audio replace --start 0.8` before
  stitching, and end outgoing speech at least 0.8s before its clip ends.
  Or stitch picture first, then lay narration on the final timeline; audio
  added after stitching is not crossfaded.

**Arguments.**
- `sources` (required, one or more) -- clips in order. `-` stands for the plan
  arriving on stdin.
- `--transition` (optional, default none) -- an xfade preset name, a
  description for a model to match to one, or a description nothing here
  matches.
- `--duration` (optional, default `0.5`) -- seconds the transition takes.
  Ignored when `--transition` is omitted.
- `--fit` (optional, default none) -- how a clip that is not this edit's size
  is resolved. `fit` preserves aspect and pads the remainder with bars, so
  nothing leaves frame. `fill` preserves aspect and crops the overflow centred,
  so nothing is letterboxed. Neither stretches. Only consulted when a size
  actually differs.
- `--model` / `--intelligence-model` (default `gpt-6-astra`) -- model for
  selection AND generation. Named presets do not call it.
- `--reasoning-effort` (default `low`) -- `low`, `medium`, `high`, `xhigh`, `max`.

**Result.** A plan with one more operation, `stitch`, written to stdout.

**Failures.** No clips named: refused, naming the fix. A `--transition` name
that matches none of the 58 presets, and that a model also cannot resolve or
prove: refused, with the expression it tried if it got that far. A clip whose
sound does not match the running edit's -- one carries an audio stream and the
other does not: refused at render, naming the file. Give the silent side a
track with `audio replace`, or say the silence is deliberate with
`audio remove` before stitching; after `audio remove` an incoming clip's sound
is dropped without complaint, because you already said what to do with it. A
clip that is not this edit's size, with no `--fit`: refused, naming both sizes
and both modes. Resizing footage without being asked changes the framing
without saying so, so there is no default.

**What it costs.** Stitching re-encodes the picture, with or without a transition.
Picture stream-copy is reserved for audio-only plans.
""",
    "overlay": """# vid overlay -- lay another clip over the picture

```bash
vid overlay inset.mp4 talk.mp4 --x 20 --y 20 --width 320 --height 180
vid overlay logo.mp4 - --x 40 --y 40 --start 0:05 --end 0:20
vid trim talk.mp4 --to 2:00 | vid overlay inset.mp4 - --x 300 --y 20
```

The FIRST argument is the clip being laid on top. The second is what it goes
over, or `-` for the plan arriving on stdin.

## Where it goes

Placement is in **pixels of the edit's own frame**, origin at the top left.
`--width` and `--height` resize the layer and must be given together: with only
one, the other would have to be invented from an aspect ratio nobody stated,
which silently reshapes the layer.

An odd width or height is rounded down to even. `yuv420p` cannot encode an odd
dimension, and the layer is composited into a frame that will be.

## When it is on screen

`--start` and `--end` bound the window. Omit both and the layer runs for the
whole edit; omit just `--end` and it runs to the end.

`--start` is when the layer APPEARS, and it plays FROM ITS OWN BEGINNING: at
`--start 0:02` the layer's first frame and first sound both arrive two seconds
in, together. It is not a seek into the layer, and `--end` bounds the window
without retiming it.

## Masks

The layer can be cut to a shape. `--mask circle` and `--mask ellipse` differ:
the circle's diameter is the layer's SHORTER side, while the ellipse is
inscribed and touches all four edges.

```bash
vid overlay cam.mp4 talk.mp4 --x 20 --y 20 --width 320 --height 180 --mask circle
vid overlay cam.mp4 talk.mp4 --mask rounded_rect --mask-radius 24 --mask-feather 3
vid overlay cam.mp4 talk.mp4 --mask video --mask-source wipe.mp4
```

`--mask image` and `--mask video` read the matte from a file: white keeps, black
drops, and a `video` matte is read **per frame**, so the cut-out can move. The
matte is scaled to the layer, so it need not match its size.

The mask is applied at the layer's own size, before any `--width`/`--height`
resize, so the shape and its feathering scale with the picture rather than being
described twice.

## Motion

An inset can grow to full screen. `--x`/`--y`/`--width`/`--height` are where it
STARTS; the `--to-*` flags are where it ends.

```bash
vid overlay cam.mp4 talk.mp4 --x 300 --y 20 --width 320 --height 180 \
  --to-x 0 --to-y 0 --to-width 1280 --to-height 720 --move-at 0:02 --move-over 1
```

**This animates presentation only.** The layer is never retimed, so its own
source timing is untouched: a moment two seconds into the recording still lands
two seconds into the recording, whatever the frame is doing around it.

Sizes are rounded to even numbers. `yuv420p` cannot encode an odd dimension, and
an animated size crosses odd values constantly.

## Transparency

`--opacity` scales the whole layer uniformly. `--key` makes part of the layer's
OWN picture transparent, by colour or by brightness -- distinct from a mask,
which imposes a shape from outside.

```bash
vid overlay cam.mp4 talk.mp4 --key colorkey --key-colour 0x00FF00
vid overlay cam.mp4 talk.mp4 --key chromakey --mask circle --opacity 0.8
```

**The order is a contract, not an implementation detail**, because a different
order looks different:

1. `--key` edits the layer's own alpha, from its own content.
2. `--mask` INTERSECTS that with a shape imposed from outside.
3. `--mask-feather` softens the COMBINED edge, not just the shape's.
4. `--opacity` scales whatever survived, uniformly.

Feathering before the intersection would soften an edge the shape then cuts
hard. Scaling opacity before the shape would make the shape's own border
semi-transparent twice.

At their defaults (`--opacity 1`, no key, no feather) these are genuine no-ops:
the emitted chain is the plain composite.

## Sound

**The layer's sound is dropped by default.** In the common picture-in-picture
case the base already carries the narration, so a layer that quietly added its
own would duplicate it. Inclusion is stated, never assumed.

```bash
--audio keep                    mix the layer in with the base
--audio only                    the layer replaces the base
--audio keep --audio-gain -6    mix it in, 6 dB down
--audio keep --duck             dip the base under the layer, recovering after
```

`keep` sums the two rather than averaging them. `amix` defaults to scaling every
input by 1/n, which drops the base 3 dB for no reason other than a layer being
present; this uses `normalize=0` and the gains you state.

The layer's sound starts when the layer appears, is bounded to the edit's own
length, and is faded 20 ms at each end so it does not click.

**Arguments.**
- `source` (required) -- the clip to lay over the picture.
- `over` (optional) -- what it goes over, or `-` for the plan on stdin.
- `--x` (optional, default `0`) -- left edge, in pixels from the frame's left.
- `--y` (optional, default `0`) -- top edge, in pixels from the frame's top.
- `--width` (optional, default none) -- resize the layer. Needs `--height` too.
- `--height` (optional, default none) -- resize the layer. Needs `--width` too.
- `--start` (optional, default none) -- when the layer appears.
- `--end` (optional, default none) -- when the layer disappears.
- `--mask` (optional, default none) -- cut the layer to a shape: `rect`,
  `rounded_rect`, `circle`, `ellipse`, `image`, `video`. None is the whole
  rectangle.
- `--mask-source` (optional, default none) -- the matte file, required for
  an `image` or `video` mask and ignored by the procedural shapes.
- `--mask-radius` (optional, default `40`) -- corner radius for `rounded_rect`.
- `--mask-invert` (optional, default off) -- cut a hole instead of a window.
- `--mask-feather` (optional, default `0`) -- soften the mask edge, in pixels.
- `--to-x` / `--to-y` (optional, default none) -- where the layer moves to.
- `--to-width` / `--to-height` (optional, default none) -- what it grows to.
  Both together or neither, for the same reason `--width`/`--height` are.
- `--move-at` (optional, default `0`) -- when the move begins.
- `--move-over` (optional, default `1`) -- seconds the move takes.
- `--easing` (optional, default `linear`) -- `linear` or `ease_in_out`.
- `--audio` (optional, default `drop`) -- the layer's sound: `drop`, `keep`
  or `only`.
- `--audio-gain` (optional, default `0`) -- layer gain in dB before mixing.
- `--base-gain` (optional, default `0`) -- base gain in dB before mixing.
- `--duck` (optional, default off) -- dip the base under the layer.
- `--key` (optional, default none) -- make part of the layer's own picture
  transparent: `colorkey`, `chromakey` or `lumakey`.
- `--key-colour` (optional, default `0x00FF00`) -- the colour to remove.
- `--key-threshold` (optional, default `0.9`) -- brightness to remove, `lumakey` only.
- `--key-similarity` (optional, default `0.3`) -- how close a pixel must be to count.
- `--key-blend` (optional, default `0`) -- softness at the keyed edge.
- `--opacity` (optional, default `1`) -- uniform transparency, 0 to 1.

**Result.** A plan with one more operation, `overlay`, written to stdout.

**Failures.** Only one of `--width`/`--height`: refused, rather than inventing
the other. A width or height that is zero or negative: refused. An `--end` at or
before `--start`: refused, because the layer would never be on screen.

**What it costs.** Compositing re-encodes the picture. Picture stream-copy is
reserved for audio-only plans.
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

**Arguments.**
- `source` (optional) -- a file to start a chain, or omit to continue a piped plan.
- `--subtitles` (required) -- an `.srt` or `.ass` file.
- `--style` (optional, default none) -- libass `force_style` syntax, e.g.
  `FontSize=28,PrimaryColour=&H00FFFFFF`.

**Result.** A plan with one more operation, `caption`, written to stdout.

**Failures.** `--subtitles` omitted: Typer refuses before this tool sees the
call (exit 2). Neither a file argument nor a plan on stdin: refused, naming the
fix. A subtitles file that does not exist: accepted here, refused later at
`render`.

**What it costs.** Nothing here. At render the `subtitles` filter forces a
re-encode of the picture -- there is no way to burn text in without redrawing
frames.
""",
    "audio": """# vid audio -- four ways to touch sound on its own

```bash
vid audio remove  talk.mp4                              a silent video
vid audio replace talk.mp4 --with narration.wav         swap the track
vid audio mix     talk.mp4 --with music.mp3             lay a bed underneath
vid audio extract talk.mp4 out.wav                      pull the track out
```

**Everything else already carries audio for you.** `trim`, `cut`, `retime` and
`stitch` move the sound with the picture automatically -- a crossfade really does
run `acrossfade` under the blend, and a cut keeps both streams locked. These four
are for the cases where you want to change the sound *itself*.

Each has its own arguments, result and failures --
`vid audio remove --help`, `vid audio replace --help`, `vid audio mix --help`,
`vid audio extract --help`.

**Arguments.** None of its own -- `audio` is a group, not a verb. Each
subcommand has its own arguments; see the four `--help`s above.

**Result.** None of its own -- see each subcommand.

**Failures.** None of its own -- see each subcommand.

**What it costs.** $0.00, no provider, no network. All of this is ffmpeg.
""",
    "audio_remove": """# vid audio remove -- drop the audio track

```bash
vid audio remove talk.mp4          # a silent video, starting a chain
vid audio remove < plan.json       # continue a piped plan
```

**Arguments.** `video` (optional) -- a file to start a chain, or omit to
continue a piped plan.

**Result.** A plan with one more operation, `audio_remove`, written to stdout.

On a video that already has no audio, this is a **no-op, not an error** -- the
operation still lands in the plan, and the render is silent either way.

**Failures.** Neither a file argument nor a plan on stdin: refused, naming the
fix. What arrives on stdin that is not JSON, or is a plan format this tool does
not speak: refused, naming what was found.

**What it costs.** Nothing here. `render` compiles this like any other
operation -- no separate decode.
""",
    "audio_replace": """# vid audio replace -- swap the audio track for another file's

```bash
vid audio replace talk.mp4 --with narration.wav
vid audio replace --with music.mp3 < plan.json     # continue a piped plan
```

**Arguments.**
- `video` (optional) -- a file to start a chain, or omit to continue a piped plan.
- `--with` (required) -- the audio file to use instead.
- `--start` (default `0.0`) -- finite, nonnegative seconds into the current
  edit to start the supplied track, not a seek into the track. Silence before
  it; an offset at or beyond the end produces silence.

**Result.** A plan with one more operation, `audio_replace`, written to stdout.

**The length question, answered rather than inherited.** The result is always
the length of the video: a shorter `--with` track is padded with silence, a
longer one is truncated. A long music file cannot quietly extend your video.
Later trims, retimes and stitches move this audio with the picture.
For incoming and outgoing transition handles, see `vid stitch --help`.

**Failures.** `--with` omitted: Typer refuses before this tool sees the call
(exit 2). Neither a file argument nor a plan on stdin: refused, naming the fix.

**What it costs.** Nothing here. `render` compiles this like any other
operation -- no separate decode.
""",
    "audio_mix": """# vid audio mix -- lay another track UNDER the existing audio

```bash
vid audio mix talk.mp4 --with music.mp3
vid audio mix talk.mp4 --with music.mp3 --level -12    # louder bed
```

**Arguments.**
- `video` (optional) -- a file to start a chain, or omit to continue a piped plan.
- `--with` (required) -- the track to lay underneath.
- `--start` (default `0.0`) -- finite, nonnegative seconds into the current
  edit to start the supplied track, not a seek into it. The original audio
  continues before this time. At or beyond the end, the supplied track is inaudible.
- `--level` (default `-18.0`) -- dB applied to the **incoming** track only, and
  negative in normal use. Your existing audio is untouched, so you adjust one
  thing rather than balancing two. There is **no automatic ducking**: a flat
  level is predictable and easy to explain; ducking is a real feature with its
  own decisions and should be asked for on purpose.

**Result.** A plan with one more operation, `audio_mix`, written to stdout. Its
length is always the video's -- a shorter `--with` track is padded with
silence, a longer one is truncated.
Later edits move it with the picture. To avoid fading narration in a transition,
reserve incoming AND outgoing handles (see `vid stitch --help`), or mix after stitching.

**Failures.** `--with` omitted: Typer refuses before this tool sees the call
(exit 2). `mix` after `remove` earlier in the same chain: refused **at
`render`**, not here -- there is nothing left to mix into, and `audio replace`
is what you want.

**What it costs.** Nothing here. `render` compiles this like any other
operation -- no separate decode.
""",
    "audio_extract": """# vid audio extract -- pull the audio out to a file

```bash
vid audio extract talk.mp4 out.wav
```

**Arguments.** `video` (required) and `output` (required, e.g. `out.wav`) --
neither is optional. This is the one audio subcommand that does not start or
continue a piped plan.

**Result.** Writes `output` and prints `wrote <output>`. It writes an audio
file, **not** a plan, so it ends a chain rather than continuing one.

**Failures.** ffmpeg not on PATH: refused, naming the fix (`vid check`).
ffmpeg's own failure: refused, showing its last line of output.

**What it costs.** One decode, no provider, no network.
""",
    "narrate": """# vid narrate -- write a narration and make it FIT

```bash
vid index talk.mp4                                    # once, first
vid narrate talk.mp4 "explain this to a new customer" --script-only
vid narrate talk.mp4 "explain this to a new customer" --out narrated.mp4
```

Writes a narration for a video, speaks it, and lays it on the clip so each line
lands on the moment it describes.

**This verb does not touch the edit-plan pipe.** `video` is always a required
argument, never a piped continuation, and the result is a human-readable report
(or a rendered file when `--out` is given) -- never JSON.

**Arguments.**
- `video` (required) -- the video to narrate. Index it first.
- `prompt` (required) -- what the narration is for, in your words.
- `--out` (optional, default none) -- render here. Omit to get the track and
  the command to lay it on yourself.
- `--script-only` (optional, default `False`) -- print the script as JSON;
  synthesise nothing.
- `--voice` (optional, default none) -- a piper voice model name (local, free),
  or `openai:<voice>[@profile]` to speak through OpenAI's TTS instead. Omit
  for the default piper voice. The `openai:` form needs an API key configured
  and sends the narration TEXT to OpenAI over the network; piper is never
  replaced with it unless you ask by name.
- `--mix` / `--replace` (optional, default none) -- force laying the narration
  over the original audio, or in place of it. Omit to decide from whether the
  video already has speech.
- `--allow-unfitted` (optional, default `False`) -- lay the narration on even
  if a line still overruns its slot after every rewrite. WITHOUT this, an
  unfitted line is a REFUSAL, not a note in the report: the run stops and names
  each line and by how much it overran. A narration that did not fit is not a
  success carrying a warning, and a report the caller must remember to read is
  not a failure.
- `--model` / `--intelligence-model` (default `gpt-6-astra`) -- model for
  script writing and every shortening retry, not the speech synthesiser.
- `--reasoning-effort` (default `low`) -- `low`, `medium`, `high`, `xhigh`, `max`.

OpenAI speech, only when explicitly selected, defaults to `gpt-4o-mini-tts`.
A profile's explicit `model` still wins. Piper remains the local default;
missing Piper never triggers a paid fallback.

**Result.** With `--script-only`, the script as JSON. With `--out`, the
rendered video at that path. Otherwise, a human-readable report naming the
narration track and the command to lay it on.

**Failures.** The video has not been indexed yet: refused, naming the fix
(`vid index`). No provider configured: refused, naming the fix (`vid check`).
No speech synthesiser installed (unless `--script-only`): refused, naming the
install command. A line that still does not fit after two rewrites and a
speed-up: REFUSED, naming each line and by how much it overran. It is not a
result carrying a warning -- `lib.narrate` returns a string, so a programmatic
caller would have nothing to branch on. Pass `--allow-unfitted` to lay the
narration on anyway and accept the overrun.

## Why it does not just write a script and read it

Generating a script, speaking it, and hoping it lands **fails at the end**, after
everything expensive has run. Thirty seconds of video under forty-five seconds of
narration is simply broken, and you find out last.

So the shot boundaries already in the index become **slots**, each with a
duration. The model writes one line per slot, told that slot's budget. Every line
is spoken and **measured**. A line that overruns is sent back to be shortened --
twice at most -- then allowed a modest speed-up, and if it still does not fit it
is **reported by name** rather than quietly overrunning.

"Does this fit?" is a number: 4.2s against a 3.0s slot. A model is never asked to
judge its own output.

## Read the script before you spend anything on it

`--script-only` prints the narration as JSON and synthesises nothing. Narration is
the most expensive thing here to get wrong, and every other artefact in this tool
is inspectable before you commit to it.

## What it does with the original audio

If the video already has speech, the narration is **mixed over** it. If it is
silent, the narration **replaces** the empty track. Force either with `--mix` or
`--replace`.

Lines land at their slot's start with **silence** between them -- never stretched
speech, which is instantly recognisable and worse than a pause. A slot shorter
than two seconds is left silent: a one-second shot cannot hold a sentence, and
cramming one in produces the rushed voiceover everybody recognises.

## What it needs

- **an index** -- `vid index` first. The narration is written against what is
  actually in the video, not guessed from its filename.
- **a speech synthesiser** -- either one, and piper is the default:
  `vid[voice]` runs **locally**, uploads nothing, and fetches its voice model
  once, anonymously. `vid[voice-openai]` with `--voice openai:<voice>` speaks
  through OpenAI's API instead, which **sends the narration text over the
  network** and costs money; it is only ever used when asked for by name.
- **a provider** -- writing the narration needs a model. `vid check` says how.

Works far better on a video that has speech or a vision index. On a silent
recording with neither, the model is told plainly that nothing is known about a
stretch, rather than left to invent something.
""",
    "recolor": """# vid recolor -- take the palette from an image

```bash
vid recolor talk.mp4 --like brand-shot.jpg | vid render out.mp4
vid recolor talk.mp4 --like brand-shot.jpg --strength 0.6 | vid render out.mp4
```

"Make this recording look like that photograph." A reference image is the only
unambiguous way to say what you want -- a named look like `--warm` is a guess at
your intent, while a picture IS your intent. It is also the only practical way to
match footage to a deck, a brand, or a clip shot somewhere else.

## What it actually does

Reinhard colour transfer. Both the video and the image are measured in a
perceptual colour space -- mean and spread, per channel -- and the transform that
moves the video's distribution onto the image's is baked into a lookup table.

The video is sampled at **nine frames** spread through it, skipping the first and
last tenth: one frame lets a single dark shot decide the whole grade, and titles
or fades at the ends are not representative of the body.

## `--strength`

`1.0` is the full transfer. A full transfer onto a very *different* reference can
look ridiculous, so this is a dial rather than a clamp you cannot see. `0.6` is a
good starting point when the reference is only loosely related to your footage.
`0` leaves the video alone.

**Arguments.**
- `video` (optional) -- the video, or omit to continue a piped plan.
- `--like` (required) -- a reference image to take the palette from.
- `--strength` (optional, default `1.0`) -- `0` leaves it alone, `1` is the
  full transfer.

**Result.** A plan with one more operation, `recolor`, written to stdout.

**Failures.** `--like` omitted: Typer refuses before this tool sees the call
(exit 2). `--like` naming a file that does not exist: refused, naming it.
Neither a file argument nor a plan on stdin: refused, naming the fix.

## What it costs

**$0.00. No provider, no network, nothing uploaded.** This is arithmetic from end
to end -- and it is worth saying plainly, because "match this video to that image"
sounds like the kind of thing that ought to need a model, and it does not.

It also costs **no extra decode.** The transform is computed once, not per frame,
and applied by ffmpeg as part of the same single pass as `trim`, `zoom` and the
rest.

## How to tell it worked

```bash
vid recolor talk.mp4 --like ref.jpg | vid render out.mp4
```

The check is arithmetic too: the output's colour statistics should sit measurably
**closer to the reference's** than the source's did. That turns "did the grade
work" from an opinion into a number.
""",
    "vignette": """# vid vignette -- darken the edges

```bash
vid vignette talk.mp4 | vid render out.mp4
vid vignette talk.mp4 --strength 0.6 | vid render out.mp4
```

Darkens the edges to pull an eye to the middle. One of three ways this tool
changes how a picture LOOKS rather than which frames you see or when -- see
`vid grade --help` and `vid lut --help` for the other two. For a recording
going in front of an audience this is not decoration: a raw screen capture is
rarely the thing you want to show.

## ORDER MATTERS, and it is yours

Applied **before** a zoom, the dark corners get zoomed INTO and vanish. Applied
**after**, it frames the zoomed result:

```bash
vid zoom talk.mp4 --to 1.5 | vid vignette | vid render out.mp4   # frames the zoom
vid vignette talk.mp4 | vid zoom --to 1.5 | vid render out.mp4   # zooms into the vignette
```

The plan is an ordered list and does exactly what you wrote, which is why this
is an operation in a chain rather than a flag on `render`.

**Arguments.**
- `video` (optional) -- a file to start a chain, or omit to continue a piped plan.
- `--strength` (optional, default `0.35`) -- `0` is nothing, `1` is theatrical.
  The default reads without announcing itself.

**Result.** A plan with one more operation, `vignette`, written to stdout.

**Failures.** Neither a file argument nor a plan on stdin: refused, naming the
fix. A `--strength` outside `0`..`1`: accepted here, refused at `render`.

**What it costs.** $0.00. No provider, no network, no model. Compiles into the
same single pass as `trim`, `zoom` and the rest at `render`.
""",
    "grade": """# vid grade -- apply a named look

```bash
vid grade talk.mp4 --look warm | vid render out.mp4
vid grade --list                      # the looks, and what each is for
```

Named, not parameterised, on purpose. `--look warm` is one decision you can
make in a second. `--warmth 0.3 --contrast 1.1 --saturation 1.2` -- four
decisions you would have no basis for -- is not something this verb offers.

Run `vid grade --list` for the current set with a line on what each is FOR.

## When a name is not enough

- **`vid recolor --like photo.jpg`** matches a reference picture. A picture is
  a far better statement of intent than any number.
- **`vid lut mylook.cube`** applies a table you already have.

## ORDER MATTERS, and it is yours

Grade **before** `stitch` and only the first clip is graded; grade **after**
and the whole joined result is. The plan is an ordered list and does exactly
what you wrote, which is why this is an operation in a chain rather than a flag
on `render`.

**Arguments.**
- `video` (optional) -- a file to start a chain, or omit to continue a piped
  plan. Ignored when `--list` is given.
- `--look` (optional, default none) -- `warm`, `cool`, `punchy`, `flat`,
  `noir`, `soft`, `bright`. Required unless `--list` is given.
- `--list` (optional, default `False`) -- print the catalogue of looks and
  what each is for. Reads no video and no piped plan, and writes no plan.

**Result.** With `--look`, a plan with one more operation, `grade`, written to
stdout. With `--list`, the catalogue as text.

**Failures.** `--look` omitted without `--list`: refused, naming the fix
(`vid grade --list`). A `--look` name not in the catalogue: accepted here,
refused at `render`, naming the valid names. Neither a file argument nor a plan
on stdin (unless `--list`): refused, naming the fix.

**What it costs.** $0.00. No provider, no network, no model. Compiles into the
same single pass as `trim`, `vignette` and the rest at `render`.
""",
    "lut": """# vid lut -- apply a lookup table you already have

```bash
vid lut mylook.cube talk.mp4 | vid render out.mp4
```

Applies a `.cube` lookup table -- the same file a color grading tool would
export -- as a filter in the same single pass as everything else.

## ORDER MATTERS, and it is yours

Same as `vignette` and `grade`: applied before a zoom it gets zoomed into;
applied after, it frames the result. The plan is an ordered list and does
exactly what you wrote, which is why this is an operation in a chain rather
than a flag on `render`.

**Arguments.**
- `table` (required) -- a `.cube` lookup table.
- `video` (optional) -- the video, or omit to continue a piped plan.

**Result.** A plan with one more operation, `lut`, written to stdout.

**Failures.** `table` omitted: Typer refuses before this tool sees the call
(exit 2). A `table` path that does not exist: accepted here, refused at
`render`, naming the path. Neither a file argument nor a plan on stdin:
refused, naming the fix.

**What it costs.** $0.00. No provider, no network, no model. Compiles into the
same single pass as `trim`, `vignette` and the rest at `render`.
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

**Arguments.**
- `video` (required) -- the rendered file to check.
- `--expect-duration` (optional, default none) -- seconds the result should be.
- `--tolerance` (optional, default `0.15`) -- how far off `--expect-duration`
  may be, in seconds.
- `--expect-resolution` (optional, default none) -- e.g. `1920x1080`.
- `--expect-audio` (optional, default `False`) -- audio present, and not silent.
- `--expect-transition-at` (optional, default none) -- a real blend, not a hard
  cut, at this second.
- `--expect-no-black-frames` (optional, default `False`) -- no long black
  stretches.
- `--longest-black` (optional, default `0.5`) -- seconds of black that is still
  acceptable. Ignored unless `--expect-no-black-frames` is given.
- `--pixel-threshold` (default `0.1`) -- black pixel luma threshold as a
  fraction `0..1` of the pixel range (ffmpeg `pix_th`).
- `--frame-threshold` (default `0.98`) -- fraction `0..1` of pixels that must
  be black to classify a frame (ffmpeg `pic_th`). Both thresholds are ignored
  without `--expect-no-black-frames` and included in its measurement report.

**Result.** A text report, one line per property checked, each showing the
measured value beside the expected one.

**Failures.** No `--expect-*` flag given at all: refused, naming the fix (name
at least one). ffmpeg missing: refused, naming the fix (`vid check`). Any
checked property violated: reported in the text, and the exit code is
non-zero.
Failed analysis, decode errors, or no decoded video frames are errors, never
"none found". Dark slides can be intentional: lower the pixel threshold or
raise the required coverage explicitly; this is detection, not a content judgment.

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
vid index talk.mp4                        # shots and speech
vid index talk.mp4 --no-speech            # shots only: free, no backend needed
vid index talk.mp4 --model small          # a bigger, slower, more accurate transcriber
```

**This verb does not touch the edit-plan pipe.** It always takes a video file as
its argument, never reads a plan from stdin, and prints a text report -- not
JSON -- so it neither continues nor starts a chain.

Produces two things and stores them where they persist:

- **shots** -- boundaries from ffmpeg scene detection. Deterministic, free, no
  provider. This is the cheapest useful work in the tool: it takes the visual
  problem from eighteen thousand frames down to a few dozen, one per shot.
- **speech** -- a transcript in time-coded passages, produced locally.

## `--model`

`--model tiny|base|small|medium` picks the faster-whisper model size. Default is
`base`: a reasonable middle ground. `tiny` is fastest and least accurate;
`medium` is the slowest and most accurate this tool exposes. Ignored when
`--no-speech` is given, since nothing is transcribed.

**The index is the expensive artifact, so it is built once and reused.** Ten
questions about one video cost one transcription. It is keyed by the video's
CONTENT, not its path -- a renamed file is the same video, a re-export is not.

## Describing what is SHOWN

```bash
vid index talk.mp4 --vision          # asks first, then describes
vid index talk.mp4 --vision --yes    # for a script or an agent
```

Without this, `find` searches only what was SAID -- so a silent screen recording
is invisible to it. `--vision` describes one frame per shot and stores the
description against that shot's existing time range, and `find` then searches
both, labelling a hit `[seen]` or `[literal]` so you know which kind of evidence
answered you.

**Shot detection is what makes this affordable.** A ten-minute recording is about
eighteen thousand frames, and describing eighteen thousand frames is absurd at any
price. Shot boundaries take it to a few dozen -- one frame each.

**The cost is stated before it is spent.** The command says how many shots it is
about to describe and waits for you to agree. In a script or an agent, where
nobody can answer, it refuses and tells you to pass `--yes` -- so nothing is ever
billed by surprise.

**No timestamp ever comes from a model.** The shot boundaries are ffmpeg's; a
model only says what a frame shows. A time is a lookup, never a guess.

**Arguments.**
- `video` (required) -- the video to index. Always a file, never a piped plan.
- `--speech` / `--no-speech` (optional, default `--speech`) -- transcribe the
  audio.
- `--model` (optional, default `base`) -- Whisper size: `tiny`, `base`,
  `small`, `medium`. Ignored when `--no-speech` is given.
- `--vision` (optional, default `False`) -- also describe what is SHOWN, one
  frame per shot.
- `--yes` (optional, default `False`) -- do not ask before spending on
  descriptions.
- `--intelligence-model` (default `gpt-6-astra`) -- vision description model.
  This does NOT change `--model`, which remains Whisper size.
- `--reasoning-effort` (default `low`) -- `low`, `medium`, `high`, `xhigh`, `max`,
  for vision descriptions only. Existing descriptions are reused, not regenerated.

**Result.** A text report: duration, fingerprint, shot count, speech passage
count, and where the index is stored.

**Declining the `--vision` prompt is a VALID PARTIAL OUTCOME, and the report
says so.** Shots and any requested speech are detected and STORED before the
prompt is shown, so answering no keeps that work rather than discarding it --
the index on disk is real and reusable, and re-running with `--vision --yes`
describes the shots without redoing detection or transcription. The report
names what was skipped ("vision descriptions skipped at your request"). This
is the one case where `index` succeeds having done less than the flags asked
for; every other shortfall below is a refusal.

**Failures.** ffmpeg or ffprobe missing: refused before any work starts,
naming the fix (`vid check`). Speech requested with no backend installed and
nothing already transcribed: refused before any work starts, naming the
install and `--no-speech`. `--vision` with no provider configured: refused,
naming the fix. `--vision` without `--yes` and not attached to a terminal:
refused, telling you to pass `--yes`.

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

Searches the index and returns a **time range grounded in indexed evidence**, with
the passage or shot description that produced it. By default it writes a plan trimmed to that range,
so it composes with everything else.

**This verb never reads a plan from stdin** -- `video` is always a required
argument, never a piped continuation. With `--show` it prints hits and evidence
instead, and writes no plan at all.

## Two tiers, same as the rest of the tool

- **literal** -- the words appear in the transcript. No model, no provider,
  $0.00. Tried first, so a caller who knows what was said never pays for a model.
- **described** -- a phrase a model matches to passages. Needs a provider.

Literal speech is tried first, then literal shot descriptions from `index --vision`
(labelled `seen`). Semantic matching uses speech when available, otherwise shot
descriptions; it does not search both semantically or inspect new frames. Speech
ids (`c0`) and shot ids (`s0`) are checked against the supplied index.

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

**Arguments.**
- `query` (required) -- what to look for, in words.
- `video` (required) -- the video to search. Always a file, never a piped plan.
- `--show` (optional, default `False`) -- print the hits and their evidence
  instead of writing a plan.
- `--model` / `--intelligence-model` (default `gpt-6-astra`) -- semantic
  matching model. Literal speech and vision-description hits do not call it.
- `--reasoning-effort` (default `low`) -- `low`, `medium`, `high`, `xhigh`, `max`.

**Result.** Without `--show`, a plan trimmed to the best hit, written to
stdout. With `--show`, the hits and their evidence as text; no plan is
written.

**Failures.** The video has not been indexed yet: refused, naming the fix
(`vid index`). Nothing matches, in either tier: refused, naming the query and
the video.
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
does not render (it may probe inputs). Use it to check what an edit will actually do, to learn the ffmpeg
behind a verb, or to hand the command to something else entirely. A tool you
cannot see through is a tool you cannot debug.

**Arguments.**
- `output` (required) -- where to write the finished video.
- `--print-command` (optional, default `False`) -- print the ffmpeg
  invocation and stop; does not render, but may probe inputs.
- `--video-codec` (default `libx264`) -- `libx264` re-encodes; `copy` preserves
  picture packets for audio-only plans (remove, replace, mix, or no operations).
  Copy rejects ALL picture/timing operations, even a no-op trim. Copy requires
  exactly one zero-start picture stream with a known positive duration and the same MP4
  or MOV extension on input and output. Other containers are not qualified.
  Supplied audio is encoded to AAC and padded/trimmed to the picture duration; no
  shortest-stream truncation. The output container may differ slightly in
  duration due to AAC framing; picture packets and their duration are not cut.

**Result.** With `--print-command`, the ffmpeg command line, printed, and
no render performed. Otherwise, `output` is written and its path is printed.

**Failures.** No plan on stdin: refused, naming the fix. ffmpeg missing from
PATH (unless `--print-command`): refused, naming the fix (`vid check`).
ffmpeg's own failure: refused, showing its last lines of output.

**What it needs.** ffmpeg on PATH to render, ffprobe when input timing or
geometry is needed (including picture copy and supplied audio).

**No model, ever.** Rendering is deterministic: the same plan and the same
inputs compile to the same ffmpeg invocation and the same output. It never
calls a provider, never needs one configured, and costs nothing beyond the
encode. The model-backed verbs are the ones that WRITE a plan (`narrate`,
`find`, `index --vision`); `render` only executes one, so a plan produced by a
model is still inspectable with `--print-command` before a frame is touched.
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

**Arguments.** None -- always reads the plan from stdin.

**Result.** The plan, unchanged, written to stdout as JSON.

**Failures.** Nothing on stdin, or stdin that is not a plan this tool speaks:
refused, naming the fix.

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

**Arguments.**
- `--describe` (optional, default `False`) -- show what each one looks like,
  not just its name.

**Result.** Without `--describe`, the preset names, space separated, one
line. With `--describe`, each name with what it looks like.

**Failures.** None -- this reads a fixed list; it does not touch ffmpeg.

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

**`use_cases`** -- the situations this tool is for, in plain language.

**`platforms`** -- which operating systems this tool runs on.

**`requires`** -- the dependencies, with `optional` stating whether the tool
works without each one:

| | |
|---|---|
| `ffmpeg` | **required.** `render`, `verify` and `index` cannot work without it. Everything that only builds a plan works fine. |
| `faster-whisper` | optional. Transcription for `index` and `find`. Runs locally; nothing is uploaded. |
| `piper-tts` | optional. Speech synthesis for `narrate`. Runs locally; nothing is uploaded. |
| `gh` + Copilot | optional. Only for *describing* a transition or a moment in words instead of naming it. |

**`body`** -- the Markdown `vid --help` renders: what the tool is for, how to
chain verbs, and where to read more.

There is no `capabilities` field here. Which verbs are model-backed is a skill
concern, not a manifest one -- see `vid --help`, which lists every verb with
that flag and points at `vid <verb> --help` for the full contract.

**For what is actually installed on THIS machine right now, run `vid check`.**
The manifest says what the tool can need; `check` says what you have.

**Arguments.** None.

**Result.** The manifest, as JSON, to stdout.

**Failures.** None in a normal install -- this only happens if the package's
own `SMART_TOOL.md` is missing or malformed, which is not a normal outcome for
an installed tool.

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

**Arguments.** None.

**Result.** A text report on this machine: what is present, what is missing,
and the install command for anything missing.

**Failures.** None -- this only inspects your machine and cannot fail.

**What it costs.** Nothing, and it needs nothing. It is a report on your machine,
not a request to anyone.
""",
}


#: Verbs that do not consume AND emit an edit plan. Each one either never reads
#: a plan on stdin, never writes one, or both -- `render` compiles instead of
#: continuing a chain, `check`/`manifest`/`transitions` report on the tool
#: rather than the video, and `index`/`narrate`/`verify`/`find` always take a
#: video argument and produce a text report (or, for `find`, a plan written but
#: never a plan read). Appending the pipe rule to any of these would tell an
#: agent the command reads stdin or costs nothing to chain when it does not.
NOT_PIPE_PARTICIPANTS = frozenset(
    {
        "render",
        "plan",
        "check",
        "manifest",
        "verify",
        "transitions",
        "index",
        "narrate",
        "find",
        "audio",  # the group overview spans three plan verbs and one non-plan verb; ambiguous, so excluded
        "audio_extract",
    }
)


def verb_doc(name: str) -> str:
    """The document for one verb, with the pipe rule appended where it applies."""
    body = _DOCS.get(name)
    if body is None:
        raise KeyError(name)
    if name in NOT_PIPE_PARTICIPANTS:
        return body.strip() + "\n"
    return body.strip() + "\n" + PIPE_RULE


def verbs() -> tuple[str, ...]:
    return tuple(_DOCS)
