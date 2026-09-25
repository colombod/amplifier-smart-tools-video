"""Write a narration, speak it, and make it FIT.

The naive shape -- generate a script, speak it, hope it lands -- fails, and it
fails at the end, after everything expensive has run. Thirty seconds of video
under forty-five seconds of narration is simply broken, and you find out last.

So this uses the shape the rest of the tool uses: THE MODEL WRITES, ARITHMETIC
JUDGES. The video is already cut into timed segments by `index`; each segment is
a slot with a duration. The model writes a line for one slot, the line is spoken,
the result is MEASURED, and a line that overruns is rewritten or slowed --
never simply allowed through.

"Does this fit?" is a number. 4.2s against a 3.0s slot. A model is never asked to
judge its own output, which is the same reason a generated transition is rendered
and sampled rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from vid.schemas import DEFAULT_INTELLIGENCE_MODEL, ReasoningEffort, VidError

#: How many times a model may rewrite one overrunning line before the tool stops
#: asking. Two is enough to catch "that was a bit long"; beyond it the model is
#: usually not going to find a shorter way to say the thing, and the honest move
#: is to slow nothing down and report the segment by name.
REWRITE_ATTEMPTS = 2

#: A line may be sped up to this before it starts sounding hurried. Past it the
#: fix is fewer words, not faster delivery.
MAX_RATE = 1.25

#: Slots shorter than this get no narration at all. A one-second shot cannot hold
#: a sentence, and cramming one in produces the rushed voiceover everybody
#: recognises and nobody wants.
MIN_SLOT = 2.0


@dataclass
class Line:
    """One segment's narration, and everything measured about it."""

    index: int
    start: float
    budget: float
    text: str = ""
    spoken: float = 0.0
    rate: float = 1.0
    attempts: int = 0
    audio: Path | None = None
    fitted: bool = False
    note: str = ""

    @property
    def overrun(self) -> float:
        return max(0.0, self.spoken - self.budget)

    def report(self) -> str:
        mark = "ok  " if self.fitted else "OVER"
        detail = f"{self.spoken:.2f}s into {self.budget:.2f}s"
        if self.rate != 1.0:
            detail += f" at {self.rate:.2f}x"
        return f"  [{mark}] {self.start:6.2f}s  {detail:<28} {self.text[:52]}"


@dataclass
class Script:
    """The whole narration, before or after it has been spoken."""

    source: str
    prompt: str
    lines: list[Line] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "source": self.source,
                "prompt": self.prompt,
                "lines": [
                    {
                        "index": line.index,
                        "start": line.start,
                        "budget": round(line.budget, 3),
                        "text": line.text,
                        "spoken": round(line.spoken, 3),
                        "rate": line.rate,
                        "fitted": line.fitted,
                        "note": line.note,
                    }
                    for line in self.lines
                ],
            },
            indent=2,
        )

    def unfitted(self) -> list[Line]:
        return [line for line in self.lines if not line.fitted and line.text]


def slots_from(record: dict) -> list[tuple[float, float]]:
    """Turn an index into narration slots.

    Shot boundaries, because they are already there and already timed -- the same
    decomposition that makes vision affordable makes narration fittable. A
    feature that reuses an existing decomposition is usually cheaper than it
    looks.

    Falls back to the whole video as one slot when nothing detected a cut, which
    is exactly what a static screen recording looks like.
    """
    shots = record.get("shots") or []
    slots = [(shot["start"], shot["end"] - shot["start"]) for shot in shots]
    slots = [(start, length) for start, length in slots if length >= MIN_SLOT]
    if not slots:
        total = record.get("duration", 0.0)
        if total < MIN_SLOT:
            raise VidError(
                f"This video is only {total:.1f}s long, which is too short to narrate. "
                f"A slot needs at least {MIN_SLOT}s to hold a sentence."
            )
        slots = [(0.0, total)]
    return slots


def _context_for(record: dict, start: float, length: float) -> str:
    """What is known to be happening during one slot.

    Speech passages that overlap it, and a shot description if vision indexing
    has run. Either may be empty -- a silent screen recording with no vision
    index gives the model nothing, and it is told so plainly rather than left to
    invent something.
    """
    end = start + length
    said = [chunk["text"] for chunk in record.get("speech", []) if chunk["start"] < end and chunk["end"] > start]
    seen = [
        shot.get("description", "")
        for shot in record.get("shots", [])
        if shot["start"] < end and shot["end"] > start and shot.get("description")
    ]
    parts = []
    if seen:
        parts.append("On screen: " + " ".join(seen))
    if said:
        parts.append("Spoken: " + " ".join(said))
    return "\n".join(parts) if parts else "(nothing is known about this stretch)"


def write_script(
    record: dict,
    prompt: str,
    intelligence,
    model: str = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: ReasoningEffort = "low",
) -> Script:
    """Ask a model for one line per slot, each with its duration budget stated."""
    from vid.intelligence import ask

    slots = slots_from(record)
    script = Script(source=record.get("video", ""), prompt=prompt)

    described = "\n\n".join(
        f"SEGMENT {i} -- starts {start:.1f}s, lasts {length:.1f}s\n{_context_for(record, start, length)}"
        for i, (start, length) in enumerate(slots)
    )
    reply = ask(
        intelligence,
        (
            "Write a narration for a video, one line per segment.\n\n"
            f"WHAT THE NARRATION IS FOR: {prompt}\n\n"
            f"{described}\n\n"
            "Rules:\n"
            "- One line per segment, in order, each on its own line as `N: text`.\n"
            "- A segment lasting S seconds fits roughly S times 2.4 words. Stay UNDER it; "
            "a line that overruns will be sent back to you to shorten.\n"
            "- Write for the ear. Short sentences. No bullet points, no markdown, no stage "
            "directions, no timestamps.\n"
            "- If a segment is better left silent, write `N: -` and nothing else.\n"
            "Nothing but the numbered lines."
        ),
        timeout_seconds=120,
        model=model,
        reasoning_effort=reasoning_effort,
    )

    written: dict[int, str] = {}
    for raw in reply.splitlines():
        head, _, tail = raw.partition(":")
        if head.strip().isdigit():
            written[int(head.strip())] = tail.strip()

    if not written:
        raise VidError(
            f"The model did not return any numbered narration lines. It said: {reply[:160]!r} "
            "Retry, or narrow the prompt so it is unambiguous that a numbered line per segment is wanted."
        )

    # OMITTED IS NOT THE SAME AS DELIBERATELY SILENT, and this used to collapse
    # the two. `written.get(i, "")` gave an absent segment the same empty text
    # as one the model explicitly marked `-`, and `fit` then stamps every empty
    # line `fitted = True, note = "left silent"` -- so `refuse_unfitted` could
    # never see them. A model that answered 2 of 20 segments produced a
    # SUCCESSFUL narration that was silent for eighteen, reported as intentional.
    #
    # The prompt asks for one numbered line per segment and documents `-` as the
    # way to say nothing. An absent ID is therefore an incomplete answer, not a
    # quiet one, and the spec is explicit that a partial result is a failure
    # unless the capability documents it as valid. This one does not.
    missing = [i for i in range(len(slots)) if i not in written]
    if missing:
        shown = ", ".join(str(i) for i in missing[:12]) + ("..." if len(missing) > 12 else "")
        raise VidError(
            f"The model answered {len(written)} of {len(slots)} narration segments, omitting "
            f"{len(missing)}: {shown}.\n"
            "An omitted segment is an incomplete answer, not a silent one -- a line that should "
            "say nothing is written `N: -`, and those are kept. Retry, or shorten the video's "
            "indexed span so fewer segments are asked for at once."
        )

    # ANSWERED EMPTILY IS NOT ANSWERED EITHER, and the check above cannot see
    # it. The fix that added it closed the OMITTED case only: `3:` and
    # `3:` followed by spaces both parse -- `head` is a digit, so the index
    # lands IN `written` with an empty tail, walks past the missing-index test,
    # becomes `text = ""`, and `fit` stamps it `fitted = True, note = "left
    # silent"`. The result is indistinguishable from the model deliberately
    # writing `3: -`, which is the one thing the distinction exists to prevent.
    #
    # A duplicate index reaches the same place by another road: `written` is a
    # dict, so a later blank `3:` overwrites an earlier real answer for 3.
    # Checking the STORED value rather than the input lines catches both.
    #
    # `-` and `--` still mean deliberate silence and are still kept -- that is
    # the documented marker in the prompt above, and the whole point is that
    # silence has to be SAID.
    blank = [i for i in range(len(slots)) if i in written and not written[i]]
    if blank:
        shown = ", ".join(str(i) for i in blank[:12]) + ("..." if len(blank) > 12 else "")
        raise VidError(
            f"The model left {len(blank)} of {len(slots)} narration segments blank -- it wrote the "
            f"number and then nothing: {shown}.\n"
            "An empty answer is an incomplete one, not a silent one. A segment that should say "
            "nothing is written `N: -`, and those are kept. Retry, or shorten the video's indexed "
            "span so fewer segments are asked for at once."
        )

    for i, (start, length) in enumerate(slots):
        text = written.get(i, "").strip()
        script.lines.append(Line(index=i, start=start, budget=length, text="" if text in {"", "-", "--"} else text))
    return script


def _shorten(
    line: Line,
    intelligence,
    model: str = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: ReasoningEffort = "low",
) -> str:
    from vid.intelligence import ask

    return (
        ask(
            intelligence,
            (
                "This narration line is too long for its slot and must be shortened.\n\n"
                f"LINE: {line.text}\n"
                f"It was spoken in {line.spoken:.1f}s and must fit {line.budget:.1f}s.\n\n"
                f"Rewrite it to be about {int(100 * line.budget / max(line.spoken, 0.1))}% as long, "
                "keeping the meaning and the tone. Reply with the rewritten line alone -- no "
                "explanation, no quotes, no label."
            ),
            timeout_seconds=60,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        .splitlines()[0]
        .strip()
        .strip('"')
    )


def fit(
    script: Script,
    speaker,
    workdir: Path,
    intelligence=None,
    model: str = DEFAULT_INTELLIGENCE_MODEL,
    reasoning_effort: ReasoningEffort = "low",
) -> Script:
    """Speak every line and make each one fit, or say which did not.

    THE LOOP THAT IS THE FEATURE. Speak, measure, and on an overrun: ask for a
    shorter line (twice at most), then try a modest speed-up, then stop and
    report. Every step is decided by a measured duration, never by a model's
    opinion of its own output.
    """
    workdir.mkdir(parents=True, exist_ok=True)

    for line in script.lines:
        if not line.text:
            line.fitted = True
            line.note = "left silent"
            continue

        out = workdir / f"line{line.index:03d}.wav"
        line.spoken = speaker.say(line.text, out)
        line.audio = out

        while line.overrun > 0 and line.attempts < REWRITE_ATTEMPTS and intelligence is not None:
            line.attempts += 1
            try:
                shorter = _shorten(line, intelligence, model=model, reasoning_effort=reasoning_effort)
            except VidError:
                break
            if not shorter or shorter == line.text:
                break
            line.text = shorter
            line.spoken = speaker.say(line.text, out)

        if line.overrun > 0:
            # Words did not come down far enough. A modest speed-up is allowed;
            # past MAX_RATE it sounds hurried and the honest answer is to report
            # the segment rather than disguise it.
            wanted = line.spoken / line.budget
            if wanted <= MAX_RATE:
                line.rate = round(wanted + 0.02, 3)
                line.spoken = speaker.say(line.text, out, rate=line.rate)

        line.fitted = line.overrun <= 0.05
        if not line.fitted:
            line.note = f"still {line.overrun:.2f}s over after {line.attempts} rewrite(s)"
    return script


def refuse_unfitted(script: Script, *, allow_unfitted: bool) -> None:
    """Stop when a line could not be fitted, unless the caller asked for it.

    A NARRATION THAT DID NOT FIT IS NOT A SUCCESS CARRYING A NOTE. The unfitted
    lines used to be appended to `narrate`'s report and that report returned
    normally, so a caller received a string and had to remember to read it. A
    report the caller must remember to read is not a failure -- and `lib.narrate`
    returns a string, so a programmatic caller had nothing to branch on at all.

    `vision.describe_shots` already refuses to return a partial set and
    `index.describe` enforces that again explicitly. This is the same rule
    reaching the one capability that was exempt from it.

    Lives here, beside `fit`, rather than inline in `lib.narrate`: the decision
    is the contract, and a contract buried in a 60-line function is one no test
    can reach without a provider and a speech engine.
    """
    unfitted = script.unfitted()
    if not unfitted or allow_unfitted:
        return
    detail = "\n".join(f"    {line.start:.2f}s -- {line.note}" for line in unfitted)
    raise VidError(
        f"{len(unfitted)} of {len(script.lines)} narration line(s) could not be fitted to "
        f"the time available:\n{detail}\n"
        "Shorten the prompt so the script has less to say, give the narration more room by "
        "trimming less, or pass --allow-unfitted to accept the overrun and lay it on anyway."
    )


def assemble(script: Script, out: Path | str, total: float) -> Path:
    """Lay every fitted line on a silent bed at its own start time.

    Silence between lines, never stretched speech: a slowed voice to fill a gap
    is instantly recognisable and worse than a pause.
    """
    import subprocess

    spoken = [line for line in script.lines if line.audio and line.text]
    if not spoken:
        raise VidError(
            "There is nothing to assemble -- no line produced any audio. Every slot was written "
            "silent, or `fit` was never run on this script. Run `vid check` to confirm the "
            "speech synthesiser is working."
        )

    inputs: list[str] = []
    parts: list[str] = []
    for position, line in enumerate(spoken):
        inputs += ["-i", str(line.audio)]
        delay = int(line.start * 1000)
        parts.append(f"[{position}:a]adelay={delay}:all=1[d{position}]")
    mixed = "".join(f"[d{i}]" for i in range(len(spoken)))
    graph = (
        ";".join(parts)
        + f";{mixed}amix=inputs={len(spoken)}:duration=longest:normalize=0[m]"
        + f";[m]apad,atrim=end={total},asetpts=PTS-STARTPTS[out]"
    )
    command = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[out]", str(out)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise VidError(
            f"Could not assemble the narration: {detail[-1] if detail else '?'}\n"
            "Check that every spoken line's audio file is readable, or run `vid check` "
            "to confirm ffmpeg is working."
        )
    return Path(out)
