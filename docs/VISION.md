# Vision

## The problem

Video editing knowledge is trapped in two places, and neither is reachable from code.

It is trapped in **editors** — Premiere, Resolve, Final Cut — where the knowledge is
muscle memory and a timeline nobody can diff. And it is trapped in **ffmpeg incantations**,
copied from Stack Overflow, that work once and are never understood. Ask someone why the
zoom stutters and they will tell you to upscale to 8000 pixels first. Ask why, and almost
nobody knows.

Meanwhile an agent asked to "cut this demo down to three minutes and highlight the pricing
section" has no path at all. It can shell out to ffmpeg and produce something, badly, by
guessing at filter syntax. It cannot know that trimming at a non-keyframe forces a
re-encode, that `atempo` only accepts a scalar so a speed ramp must be segmented, or that
chaining five operations naively costs five decodes and five encodes for no reason.

That knowledge is real, it is learnable, and it currently has to be learned by every
person and every agent, separately, forever.

## What this is

A tool that holds video-editing expertise so its caller does not have to.

The caller says what it wants — trim here, speed up there, stitch these, find the part
where she explains pricing — and receives an edit it can inspect, diff, and hand to a
renderer. The tool owns the parts that are actually hard: which operations can avoid
re-encoding, how to express an intent as one filter graph instead of five, and where a
model's judgment is needed versus where arithmetic will do.

## Who it is for

- **An agent** asked to edit video, which today has no honest option between "guess at
  ffmpeg" and "refuse".
- **A script or CI job** producing video mechanically — release demos, clip reels, social
  cuts — where an interactive editor is not available and should not be.
- **A person** who knows what they want and does not want to learn `zoompan`'s expression
  language to get it.

## What it refuses to be

**Not an editor.** No timeline UI, no scrubbing, no preview window. Those are solved, and
solved better, by tools built for humans with mice.

**Not a wrapper that hides ffmpeg.** The edit it produces is inspectable, and the command
it runs can be printed. A tool you cannot see through is a tool you cannot debug, and
video work fails in specific, physical ways — a codec that will not copy, a fade that
needs matching frame rates — that a caller sometimes has to see.

**Not a magic "make it good" button.** The model is used where judgment is genuinely
required and nowhere else. Cutting at 10.5 seconds is arithmetic. Deciding *which* ten
seconds are worth keeping is not.

## Where a model is allowed to help

"Where judgment is required" is true and useless — it gives nobody a way to decide a new
case. The line this tool actually holds is sharper:

> **A model is asked to CHOOSE from things that exist. It is not asked to INVENT one.**

Choosing is safe because a wrong choice is *detectable*. Asked which of fifty-eight
transitions suits "soft and dreamy", a model returns a name we can check against the list;
an answer that is not on it is a loud error. Asked which passage of a transcript discusses
pricing, it returns a segment we already timed; the timestamp was never its to produce.

Inventing has no such floor. A model asked for a timestamp returns a plausible number that
is indistinguishable from a correct one until somebody watches the video. That is not a
model being unreliable — it is us asking a question whose wrong answers look exactly like
right ones.

So the tool's commitment is not "we use AI carefully". It is that **every model-backed
capability either draws from a closed set, or ships with a way to check its output.** A
capability that can do neither does not get built, however useful it sounds.

## An edit you can read before it happens

Everything this tool does is written down before anything is rendered. A model that helps
compose an edit leaves its reasoning in the plan alongside its choice, and a person reads
both before a single frame moves.

That is the difference between a tool that edits video for you and one you could ever
trust with footage you cannot re-shoot. Not confidence in the model — the ability to
disagree with it in time.

## The bet

The interesting operations are not the mechanical ones. Anyone can trim at a timestamp.

The valuable request is **"trim to where she explains pricing"** — and that is only
answerable if the tool can build a time-indexed account of what is *in* a video: what was
said and when, what changed on screen and when. That index is the expensive artifact, and
building it once makes every subsequent question cheap.

This is where a smart tool earns its name. Not by calling a model to do arithmetic, but by
owning the difference between a question arithmetic can answer and a question that needs
evidence.
