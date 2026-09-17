# Can a model write a transition? — experiment 5x2

**Answer: yes, five times out of six — and the one failure is the interesting part,
because it was invisible in the syntax and arithmetic caught it.**

## What was run

ffmpeg's `xfade` has a `custom` transition taking an expression over two inputs, with
variables `X`, `Y`, `W`, `H`, `P` (progress 0→1), `PLANE`, `A` and `B` (the two source
pixels). Six plain-language descriptions were handed to a model, which produced one
expression each. Every result was rendered against the red/green fixtures and graded by
`vid verify`'s property checks — **not** by the model, and **not** by anyone watching.

**Who the model was.** Claude, directly, rather than the tool's configured intelligence
backend. So this measures what a capable model can produce, not what the shipped path
produces. Recorded because it materially qualifies the result.

## Results

| description | verdict |
|---|---|
| a plain crossfade | **correct** |
| a hard edge sweeping in from the right | **correct** |
| fade down to black, then up into the second clip | **valid but wrong** |
| the second clip opens in a circle growing from the centre | **correct** |
| a diagonal wipe from the top-left corner | **correct** |
| the second clip slams in from the right, overshooting slightly | **correct** |

**Nothing failed to parse.** Every expression was syntactically valid ffmpeg. The
category we most feared — a model that cannot produce compilable filter code — did not
occur once.

## The failure, which is the finding

```
if(lt(P,0.5), A*(1-2*P), B*(2*P-1))
```

Read that as a person and it is obviously right: fade A down to zero over the first half,
bring B up from zero over the second. Zero is black.

**It is not black.** `xfade` evaluates the expression **once per plane** of a YUV image,
not over RGB. Driving every plane to zero gives `Y=0` — black luma — but also `U=0, V=0`,
which is not neutral grey; it is **extreme chroma**. Neutral is 128.

Sampled through the window, the "fade to black" never darkened:

```
1.70s  RGB(253, 13, 13)     red, clip A
2.25s  RGB(  0,135,  0)     already green — not black, not red
2.50s  RGB(  0,135,  0)     the "black" midpoint
3.00s  RGB( 17,135, 16)     green, clip B
```

The property check graded it `FAIL — the middle frame matches one side`, and it was right.

The corrected expression keeps luma and chroma apart:

```
if(lt(P,0.5),
   if(eq(PLANE,0), A*(1-2*P), 128+(A-128)*(1-2*P)),
   if(eq(PLANE,0), B*(2*P-1), 128+(B-128)*(2*P-1)))
```

```
2.25s  RGB(  0, 52,  0)
2.50s  RGB(  0,  0,  0)     genuinely black
2.75s  RGB(124,  0,  0)
```

`verify` passes it: `mid-frame 254 from before, 137 from after`.

## What this establishes

**The model's error was a domain error, not a coding error.** The expression was
well-formed, plausible, and would survive any review by someone who did not happen to know
that `xfade` works per-plane in YUV. That is precisely the class of mistake a human
reviewer waves through and a renderer executes without complaint.

**Arithmetic caught what reading would not.** No model graded this. Three sampled frames
and a distance comparison separated a real transition from a convincing-looking one.

**The division of labour is the result.** A model is good at turning "a circle growing from
the centre" into `hypot(X-W/2, Y-H/2) < P*hypot(W/2,H/2)` — genuinely useful work that
would otherwise need someone to learn an expression language. A model is not good at
knowing whether its own output moved correctly. Those are different jobs and they should
be done by different things.

## What would have to be true to ship this

1. **A generated expression is never cached as trusted.** It is verified per render, or it
   is not used.
2. **Failing the property check must refuse, not warn.** A transition that silently falls
   back to `fade` when generation fails is the worst outcome available: the caller asked
   for something specific and gets something generic, unremarked.
3. **The check needs a case it currently lacks.** A fade *through a third state* passes
   only because black resembles neither side. A transition passing through a colour that
   happens to resemble one source would be graded wrongly. Sampling more points across the
   window — and requiring monotonic progression rather than one midpoint — closes that.

Point 3 is a real limitation of `verify` as it stands, found by this experiment rather
than by inspecting it.
