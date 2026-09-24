"""Compile a plan into ONE ffmpeg invocation.

This is where the architecture pays off. Every verb before this one appended to a
plan without touching a frame; this module turns the whole plan into a single
filter graph, so a chain of five operations costs one decode and one encode
rather than five of each.

ARGV, NEVER A SHELL STRING. Everything here returns a list of arguments. A filter
graph is full of colons, commas, quotes and backslashes, and a Windows path
carries a drive-letter colon that ffmpeg's own filter syntax reads as a
separator. Building a shell string would mean solving that quoting problem three
times, once per platform, and getting it wrong somewhere. A list is passed to the
OS unparsed, so there is no quoting problem to get wrong.
"""

from __future__ import annotations

import hashlib
import itertools
import math
from pathlib import Path
import re

from vid.plan import (
    AudioMix,
    AudioRemove,
    AudioReplace,
    Caption,
    Cut,
    Grade,
    Lut,
    Overlay,
    Plan,
    Recolor,
    Retime,
    Stitch,
    Trim,
    Vignette,
    Zoom,
)
from vid.schemas import VidError

#: `atempo` is documented as reliable in this range. Outside it, chain stages.
ATEMPO_MIN, ATEMPO_MAX = 0.5, 2.0

#: How finely a retime ramp between two control points is subdivided.
#: `atempo` takes a scalar, so a speed ramp cannot be expressed as a single
#: ffmpeg expression -- it is approximated as this many constant pieces. More
#: pieces follow the curve more closely at the cost of a larger filter graph.
RAMP_STEPS = 8


def lut_cache_dir() -> Path:
    """Where generated recolor LUTs persist, keyed by the numbers that produced
    them. Same convention as `index.index_dir()` and `voice.voices_dir()`: an
    env override, then XDG, never the shared temp directory."""
    import os

    override = os.environ.get("VID_LUT_CACHE_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "vid" / "luts"


def atempo_chain(speed: float) -> list[str]:
    """`atempo` stages whose product is `speed`.

    One filter cannot go past 2x or below 0.5x, so a 4x change is two stages. The
    chain is built to use as few stages as possible, because each one resamples
    and each resample costs a little audio quality.
    """
    if speed <= 0:
        raise VidError(f"Speed must be greater than zero; got {speed}.")
    stages: list[str] = []
    remaining = speed
    while remaining > ATEMPO_MAX:
        stages.append(f"atempo={ATEMPO_MAX}")
        remaining /= ATEMPO_MAX
    while remaining < ATEMPO_MIN:
        stages.append(f"atempo={ATEMPO_MIN}")
        remaining /= ATEMPO_MIN
    if abs(remaining - 1.0) > 1e-9:
        stages.append(f"atempo={remaining:.6f}".rstrip("0").rstrip("."))
    return stages or ["atempo=1.0"]


def ramp_segments(ramp: list, total: float | None = None) -> list[tuple[float, float, float]]:
    """A speed curve, decomposed into constant-speed regions.

    `atempo` takes a SCALAR. There is no time-varying audio tempo filter in
    ffmpeg, and no expression language for one. So a ramp is not expressible as a
    filter at all -- it has to become a sequence of constant-speed pieces that are
    retimed separately and rejoined.

    Returns `(start, end, speed)` triples. The speed of a region is the mean of
    its endpoints, which is the straightforward reading of a linear ramp between
    two control points.
    """
    if len(ramp) < 2:
        raise VidError(
            "A ramp needs at least two control points -- a speed to start from and one to reach. "
            "For a single constant speed, use --speed instead."
        )
    points = sorted(ramp, key=lambda p: p.at)
    segments: list[tuple[float, float, float]] = []
    for first, second in itertools.pairwise(points):
        if second.at <= first.at:
            raise VidError(f"Ramp control points must advance in time; {first.at} and {second.at} do not.")
        # SUBDIVIDED, and the first attempt at this was wrong in a way worth
        # recording. Taking one segment per control-point pair at the MEAN of its
        # endpoints made a ramp 1x -> 0.25x -> 1x compile to two segments of
        # 0.625x each -- arithmetically correct, and indistinguishable from a
        # constant 0.625x. The ramp disappeared into its own average.
        #
        # So each pair is subdivided and each piece takes the speed at its
        # midpoint. The curve is still piecewise-constant, because `atempo` gives
        # us nothing else, but it now follows the shape it was asked for.
        span = second.at - first.at
        steps = max(1, min(RAMP_STEPS, int(span * 2)))
        for index in range(steps):
            lo = first.at + span * index / steps
            hi = first.at + span * (index + 1) / steps
            position = (index + 0.5) / steps
            speed = first.speed + (second.speed - first.speed) * position
            segments.append((lo, hi, speed))
    if total is not None and points[-1].at < total:
        segments.append((points[-1].at, total, points[-1].speed))
    return segments


class Compiler:
    """Turns a plan into inputs plus a filter graph."""

    def __init__(
        self,
        plan: Plan,
        durations: dict[str, float] | None = None,
        has_audio: bool = True,
        frame_rate: float | None = None,
        dimensions: tuple[int, int] | None = None,
        source_audio: dict[str, bool] | None = None,
        source_sizes: dict[str, tuple[int, int]] | None = None,
    ) -> None:
        if plan.source is None:
            raise VidError("This plan has no source video. Name one when the chain starts.")
        self.plan = plan
        # Clip lengths, supplied by the render path. Absent them a transition
        # cannot be placed, and the compiler says so rather than guessing an
        # offset that would silently put the blend in the wrong place.
        self.durations = durations or {}
        self.elapsed = self.durations.get(plan.source, 0.0)
        self.inputs: list[str] = [plan.source]
        self.filters: list[str] = []
        self.video = "0:v"
        # None when the source carries no audio stream. Every audio step is
        # already guarded on this, so the whole chain degrades to video-only
        # rather than emitting a `-map 0:a` that ffmpeg cannot satisfy.
        self.audio = "0:a" if has_audio else None
        #: Path -> whether that file carries an audio stream, for the clips a
        #: stitch pulls in. Probed by the render path for the same reason
        #: `durations` is: it is a fact about the file, not about the plan, and
        #: keeping it out of here is what lets every other verb run with no
        #: ffmpeg at all. A path that is absent is assumed to have sound.
        self.source_audio = source_audio or {}
        #: Path -> (width, height) for the clips a stitch pulls in, probed by
        #: the render path alongside `durations` and `source_audio`. Absent
        #: means nobody probed, and nothing is resized on an unproven guess.
        self.source_sizes = source_sizes or {}
        #: The size every stitched clip is resolved to: the plan source's own.
        #: Taken from the probed sizes rather than `dimensions` so that a plan
        #: compiled without probing has no target and normalises nothing.
        self.target_size = self.source_sizes.get(plan.source)
        #: True once `audio remove` has run. See `audio_remove`.
        self.audio_removed = False
        #: The source's own frame rate, used to put retimed frames back on a
        #: uniform grid, and to give `zoompan` an accurate per-frame clock.
        #: None when it could not be read, in which case retime behaves as
        #: it always did rather than guessing a rate, and zoom refuses.
        self.frame_rate = frame_rate
        #: The source's own (width, height), used to pin `zoompan`'s output
        #: size to it. None when it could not be read.
        self.dimensions = dimensions
        self._label = 0

    def _next(self, prefix: str) -> str:
        self._label += 1
        return f"{prefix}{self._label}"

    def _step(self, chain: str, src: str, prefix: str) -> str:
        out = self._next(prefix)
        self.filters.append(f"[{src}]{chain}[{out}]")
        return out

    def _astep(self, chain: str) -> None:
        """Apply an audio filter, unless the audio has been removed.

        Every verb that touches time -- trim, cut, retime -- moves audio
        alongside video. Once `audio remove` has run there is no audio to move,
        and passing the absent stream through anyway produced a filter graph
        containing the literal string "None", which ffmpeg rejects with an error
        naming neither the verb nor the cause.

        Unreachable until audio-only operations existed, and invisible to a type
        checker at runtime because `from __future__ import annotations` makes the
        annotation lazy. Caught by asking what `audio remove` followed by `trim`
        actually compiles to.
        """
        if self.audio is not None:
            self.audio = self._step(chain, self.audio, "a")

    # -- operations ---------------------------------------------------------

    def trim(self, op: Trim) -> None:
        end = f":end={op.end}" if op.end is not None else ""
        self.video = self._step(f"trim=start={op.start}{end},setpts=PTS-STARTPTS", self.video, "v")
        aend = f":end={op.end}" if op.end is not None else ""
        self._astep(f"atrim=start={op.start}{aend},asetpts=PTS-STARTPTS")
        # An explicit end bounds the result exactly, known without probing
        # anything -- it is arithmetic on the numbers this op already carries.
        # Without one, the result is still exact whenever the length coming in
        # was already known; otherwise it stays the 0.0 "unknown" sentinel
        # `_bound_audio` already treats as falsy, same as before this op ran.
        if op.end is not None:
            self.elapsed = op.end - op.start
        elif self.elapsed:
            self.elapsed -= op.start

    def cut(self, op: Cut) -> None:
        """Remove a range by keeping what is either side of it and rejoining."""
        head_v = self._step(f"trim=start=0:end={op.start},setpts=PTS-STARTPTS", self.video, "v")
        tail_v = self._step(f"trim=start={op.end},setpts=PTS-STARTPTS", self.video, "v")
        # The removed span's length is always known; the RESULT's is only
        # knowable when the length coming in already was (the tail's own
        # length needs the total, which nothing here probes).
        if self.elapsed:
            self.elapsed -= op.end - op.start
        if self.audio is None:
            # A silent video still cuts. concat's a=0 form takes video only --
            # feeding it an absent stream put the literal string "None" in the
            # graph and ffmpeg rejected the whole command.
            out_v = self._next("v")
            self.filters.append(f"[{head_v}][{tail_v}]concat=n=2:v=1:a=0[{out_v}]")
            self.video = out_v
            return
        head_a = self._step(f"atrim=start=0:end={op.start},asetpts=PTS-STARTPTS", self.audio, "a")
        tail_a = self._step(f"atrim=start={op.end},asetpts=PTS-STARTPTS", self.audio, "a")
        out_v, out_a = self._next("v"), self._next("a")
        self.filters.append(f"[{head_v}][{head_a}][{tail_v}][{tail_a}]concat=n=2:v=1:a=1[{out_v}][{out_a}]")
        self.video, self.audio = out_v, out_a

    def _bound_audio(self, expected: float | None) -> str:
        """Pad-then-trim the audio to the duration the edit actually means.

        atempo does not land exactly. Speeding up, its output runs LONG (1.5066s
        for a 1.500s edit); slowing down, it runs SHORT (5.986s for 6.000s). The
        sign flips, which is precisely why the obvious `-shortest` cannot fix it
        -- a flag that always takes the shorter stream turns one error into the
        other.

        apad covers the short case, atrim the long one, and the result follows
        the video rather than the resampler's rounding.

        Empty string when the source duration is unknown, so a plan compiled
        without probing behaves exactly as it always did.
        """
        if not expected:
            return ""
        return f",apad,atrim=end={expected:.6f},asetpts=PTS-STARTPTS"

    def _regrid(self) -> str:
        """`fps=` to append after a `setpts`, or nothing when the rate is unknown.

        setpts rescales timestamps but leaves frames off the uniform grid the
        container expects, so the duration lands long -- 1.567s where 1.500s was
        asked for, a 4.7% overshoot. Resampling to the SOURCE's own rate fixes it
        exactly (45 frames, 1.500000s) without changing the frame rate a caller
        chose.

        Empty when the rate could not be read: reverting to the old, slightly
        long behaviour is better than imposing a guessed frame rate on someone's
        footage.
        """
        return f",fps={self.frame_rate:g}" if self.frame_rate else ""

    def retime(self, op: Retime) -> None:
        if op.speed is not None:
            self.video = self._step(f"setpts={1 / op.speed:.6f}*PTS{self._regrid()}", self.video, "v")
            self._astep(
                ",".join(atempo_chain(op.speed)) + self._bound_audio(self.elapsed / op.speed if self.elapsed else None)
            )
            if self.elapsed:
                self.elapsed /= op.speed
            return

        segments = ramp_segments(op.ramp)
        retimed_total = sum((end - start) / speed for start, end, speed in segments)
        parts: list[str] = []
        for start, end, speed in segments:
            seg_v = self._step(
                f"trim=start={start}:end={end},setpts=PTS-STARTPTS,setpts={1 / speed:.6f}*PTS{self._regrid()}",
                self.video,
                "v",
            )
            if self.audio is None:
                parts.append(f"[{seg_v}]")
                continue
            seg_a = self._step(
                f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS," + ",".join(atempo_chain(speed)),
                self.audio,
                "a",
            )
            parts += [f"[{seg_v}]", f"[{seg_a}]"]
        if self.audio is None:
            out_v = self._next("v")
            self.filters.append(f"{''.join(parts)}concat=n={len(segments)}:v=1:a=0[{out_v}]")
            self.video = out_v
            return
        out_v, out_a = self._next("v"), self._next("a")
        self.filters.append(f"{''.join(parts)}concat=n={len(segments)}:v=1:a=1[{out_v}][{out_a}]")
        self.video, self.audio = out_v, out_a
        self.elapsed = retimed_total
        bound = self._bound_audio(retimed_total)
        if bound:
            self.audio = self._step(bound.lstrip(","), self.audio, "a")

    def zoom(self, op: Zoom) -> None:
        """Ken Burns, as ffmpeg's own purpose-built filter -- ONE node.

        `zoompan` looked unusable for this at first, for two real reasons:
        its `d` is OUTPUT FRAMES PER INPUT FRAME CONSUMED, not the effect's
        length (an 8s/25fps source measured out at 400s when `d` was set to
        `duration * fps`), and with no explicit `s=` its output silently
        defaults to `hd720` regardless of the source (a 640x360 clip came
        out 320x180, because a trailing `scale=iw/4:-2` made it worse).
        Both are answers, not workarounds: `d=1` emits exactly one output
        frame per input frame, so the frame count -- and hence the
        duration, at the source's own `fps=` -- is untouched; `s=` pinned to
        the source's own dimensions keeps the frame size untouched too.

        That replaces a PREVIOUS fix that dodged both bugs by decomposing
        the ramp into constant-zoom pieces -- trim, split, crop against a
        scale2ref'd reference, nullsink, setsar, concat -- ten segments and
        over fifty filter-graph entries for one zoom. `zoompan` recomputes
        its `zoom` expression every INPUT frame, continuously, so there is
        no ramp to decompose into pieces in the first place, and nothing
        of different sizes or SARs for `concat` to reconcile. That
        reconciliation is exactly the part a stable ffmpeg refused (`concat`
        rejecting a SAR mismatch scale2ref did not promise not to
        introduce) while a bleeding-edge build happened to tolerate it --
        so this is not a fix for that tolerance gap, it removes the graph
        shape that created it.

        `time` gates the ramp to `[at, at + duration]`: it is `zoompan`'s
        own per-output-frame clock, `frame_count / fps` -- and `fps` is set
        to the source's actual rate, so it is seconds since this stage's
        own PTS-zero, matching what `at`/`duration` mean elsewhere in this
        module. `op.x`/`op.y` are passed through UNCHANGED: `zoompan` defines
        `zoom` as the just-evaluated zoom level for the current frame, which
        is exactly the variable the plan's default expressions already name
        (see `Zoom` in plan.py) -- there is no substitution left to do.

        Needs the source's frame rate and dimensions, which this compiler
        does not have on its own; `render` probes them for exactly this
        reason (see `needs_durations` in lib.py).
        """
        if self.frame_rate is None or self.dimensions is None:
            raise VidError(
                "zoom needs to know the source's frame rate and dimensions to pin "
                "`zoompan`'s output, and neither was supplied. Compile through `vid render`, "
                "which probes them from the source file."
            )
        at = op.at if op.at is not None else 0.0
        duration = max(op.duration, 0.0)
        to = op.to
        if duration > 0:
            zoom_expr = (
                f"if(lt(time,{at:.6f}),1,"
                f"if(lt(time,{at + duration:.6f}),1+({to:.6f}-1)*(time-{at:.6f})/{duration:.6f},{to:.6f}))"
            )
        else:
            # No ramp to speak of -- an instant step at `at`, held either side.
            zoom_expr = f"if(lt(time,{at:.6f}),1,{to:.6f})"
        width, height = self.dimensions
        self.video = self._step(
            f"zoompan=z='{zoom_expr}':x='{op.x}':y='{op.y}':d=1:s={width}x{height}:fps={self.frame_rate:g}",
            self.video,
            "v",
        )

    def stitch(self, op: Stitch) -> None:
        for source in op.sources:
            if source == "-":
                continue
            self.inputs.append(source)
            index = len(self.inputs) - 1
            other_v, other_a = f"{index}:v", f"{index}:a"
            # Whether a clip carries sound is a property of the FILE, probed
            # before compiling. None means nobody probed -- a direct
            # `compile_plan` call rather than the render path -- and an unproven
            # guess must not raise. Unknown follows the running edit, which is
            # exactly how this behaved before anything probed the sources.
            known = self.source_audio.get(source)
            if known is not None:
                self._refuse_audio_mismatch(source, known)
            other_v = self._normalised(source, other_v, op)
            if op.transition:
                self._transition(other_v, other_a, op)
                self.elapsed += self.durations.get(source, 0.0) - op.transition_duration
            elif self.audio is None:
                # Neither side has sound. concat's a=0 form takes video only;
                # interpolating the absent stream anyway put the literal string
                # "None" in the graph and ffmpeg rejected the whole command.
                # Exactly the guard `cut` already carries.
                self.elapsed += self.durations.get(source, 0.0)
                out_v = self._next("v")
                self.filters.append(f"[{self.video}][{other_v}]concat=n=2:v=1:a=0[{out_v}]")
                self.video = out_v
            else:
                self.elapsed += self.durations.get(source, 0.0)
                out_v, out_a = self._next("v"), self._next("a")
                self.filters.append(
                    f"[{self.video}][{self.audio}][{other_v}][{other_a}]concat=n=2:v=1:a=1[{out_v}][{out_a}]"
                )
                self.video, self.audio = out_v, out_a

    def overlay(self, op: Overlay) -> None:
        """Lay another clip over the picture at a stated place and time.

        Picture only. The layer's sound is not taken, which is the whole point
        of stating it: in the common picture-in-picture case the base already
        carries the narration, and quietly mixing a second copy in is the
        defect, not the feature. `self.audio` is therefore never read here --
        it cannot leak the literal string "None" into the graph the way the
        stitch path once did, because it is not interpolated at all.

        `elapsed` is untouched. An overlay changes what a frame LOOKS like, not
        how many there are, so a duration that moved would be a defect.
        """
        self.inputs.append(op.source)
        layer = f"{len(self.inputs) - 1}:v"

        if (op.width is None) != (op.height is None):
            raise VidError(
                "An overlay needs both --width and --height, or neither. Given only one, "
                "the other would have to be invented from an aspect ratio nobody stated, "
                "which silently reshapes the layer."
            )
        if op.width is not None and op.height is not None:
            # Even dimensions: yuv420p cannot encode an odd width or height, and
            # an overlay is composited into a frame that will be.
            width, height = (op.width // 2) * 2, (op.height // 2) * 2
            layer = self._step(f"scale={width}:{height},setsar=1", layer, "v")

        # `enable` is what confines the layer to its window. Without it the
        # overlay runs for the whole edit regardless of what was asked.
        window = ""
        if op.start is not None or op.end is not None:
            start = op.start if op.start is not None else 0.0
            window = (
                f":enable='between(t,{start:.6f},{op.end:.6f})'"
                if op.end is not None
                else f":enable='gte(t,{start:.6f})'"
            )

        out = self._next("v")
        self.filters.append(f"[{self.video}][{layer}]overlay=x={op.x}:y={op.y}{window}[{out}]")
        self.video = out

    def _normalised(self, source: str, label: str, op: Stitch) -> str:
        """Resize a clip to the target size, or return it untouched.

        `concat` requires matching resolution and SAR across segments. Without
        this the mismatch reaches ffmpeg, which rejects the whole command rather
        than naming the file, so a caller learns about it at render time in a
        message that does not say which clip was the problem.

        Both modes preserve aspect ratio. Stretching is never applied: a frame
        that silently changed shape looks plausible and is wrong, which is the
        class of defect this whole compiler keeps paying for.

        `setsar=1` because matching pixel dimensions is not sufficient -- two
        clips of the same size and different sample aspect are still refused.
        """
        target = self.target_size
        size = self.source_sizes.get(source)
        if target is None or size is None or size == target:
            # Unknown means nobody probed, which is the direct `compile_plan`
            # path. An unproven guess must not resize someone's footage.
            return label
        if op.fit is None:
            raise VidError(
                f"Cannot stitch {source!r}: it is {size[0]}x{size[1]} and this edit is "
                f"{target[0]}x{target[1]}. Say how to resolve that with `--fit fit` "
                "(preserve aspect, pad the remainder with bars, nothing leaves frame) or "
                "`--fit fill` (preserve aspect, crop the overflow centred, nothing is "
                "letterboxed). Neither is a default, because resizing footage without "
                "being asked changes the framing without saying so."
            )
        width, height = target
        if op.fit == "fit":
            chain = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
            )
        else:
            chain = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
        return self._step(chain, label, "v")

    def _refuse_audio_mismatch(self, source: str, incoming_has_audio: bool) -> None:
        """Refuse a join where exactly one side carries sound.

        concat needs the same streams on both sides. Passing it a specifier for
        a stream that does not exist fails as `Stream specifier ... matches no
        streams`, which names neither the file nor the reason -- and dropping
        the sound instead would silently discard audio the caller still has.

        Named here, before ffmpeg runs, because the caller knows which file they
        meant and ffmpeg does not.
        """
        running_has_audio = self.audio is not None
        if running_has_audio == incoming_has_audio:
            return
        if self.audio_removed:
            # `audio remove` already said what to do with sound in this edit.
            # Dropping the incoming clip's too is carrying out that instruction,
            # not discarding something silently, so there is nothing to refuse.
            return
        if incoming_has_audio:
            raise VidError(
                f"Cannot stitch {source!r}, which has sound, onto an edit that has none. "
                "Give the running edit an audio track with `vid audio replace`, or say the "
                "silence is deliberate with `vid audio remove` before stitching -- joining "
                "a silent edit to a sounded clip would have to invent a track or discard one."
            )
        raise VidError(
            f"Cannot stitch {source!r} onto this edit: it carries no audio stream, and the "
            "edit so far does. Add sound to it with `vid audio replace`, or remove the "
            "edit's own with `vid audio remove` before stitching -- joining them as they "
            "are would silently drop the audio you already have."
        )

    def _transition(self, other_v: str, other_a: str, op: Stitch) -> None:
        """`xfade` for video, `acrossfade` for audio -- they are separate filters.

        `xfade` is video-only. A caller who forgets that gets a picture that
        blends over an audio track that hard-cuts, which sounds like a mistake
        because it is one.

        `offset` is measured from the start of the FIRST input, so it must be the
        running duration minus the overlap. We do not know that duration without
        probing, so the offset is resolved by the caller before compiling.
        """
        if not self.durations:
            raise VidError(
                "A transition needs to know how long the clips are, and no durations were "
                "supplied. Compile through `vid render`, which probes them -- a transition "
                "placed at a guessed offset blends in the wrong place and looks like a bug."
            )
        out_v, out_a = self._next("v"), self._next("a")
        offset = max(0.0, self.elapsed - op.transition_duration)
        expr = ""
        if op.transition == "custom":
            if not op.transition_expr:
                raise VidError(
                    "A custom transition needs an expression, and this plan carries none. "
                    "Custom transitions are generated and verified when the plan is built -- "
                    "rebuild it through `vid stitch --transition <description>` rather than "
                    "hand-editing the plan JSON."
                )
            if not op.transition_verified:
                raise VidError(
                    "This plan carries an UNVERIFIED custom transition. A generated "
                    "expression reaches a plan only after being proven to blend against "
                    "the clips it will be used on -- refusing rather than rendering "
                    "something whose behaviour nobody checked. Rebuild it through "
                    "`vid stitch --transition <description>`, which generates and verifies "
                    "before writing it into a plan."
                )
            expr = f":expr='{op.transition_expr}'"
        self.filters.append(
            f"[{self.video}][{other_v}]"
            f"xfade=transition={op.transition}:duration={op.transition_duration}:offset={offset}"
            f"{expr}[{out_v}]"
        )
        if self.audio is None:
            # Silent on both sides -- `_refuse_audio_mismatch` has already ruled
            # out the one-sided case. xfade is video-only, so the picture blend
            # above is the whole transition; acrossfade below would interpolate
            # the absent stream the same way the plain concat path used to.
            self.video = out_v
            return
        # AAC decode padding is not a transition handle. Bound both sides to
        # their picture timing before acrossfade, or each join drifts later.
        self._astep("aresample=async=1:first_pts=0" + self._bound_audio(self.elapsed))
        other_a = self._step(
            "aresample=async=1:first_pts=0" + self._bound_audio(self.durations.get(self.inputs[-1])),
            other_a,
            "a",
        )
        self.filters.append(f"[{self.audio}][{other_a}]acrossfade=d={op.transition_duration}[{out_a}]")
        self.video, self.audio = out_v, out_a

    def caption(self, op: Caption) -> None:
        # Escaping matters here: a Windows path carries a drive-letter colon that
        # the filter parser reads as an argument separator.
        from vid.probe import has_subtitles_filter

        if not has_subtitles_filter():
            raise VidError(
                "This ffmpeg was built without libass, so it has no `subtitles` filter and "
                "cannot burn captions into the picture.\n"
                "  Every other verb works on this build -- `caption` is the only one affected.\n"
                "  macOS:  brew install ffmpeg   (the full formula; some taps ship a slim one)\n"
                "          then, if captions render without text, brew may have left its\n"
                "          font stack unconfigured -- libass finds fonts through fontconfig:\n"
                "            brew postinstall ca-certificates fontconfig gnutls glib openssl@3\n"
                "  Linux:  apt install ffmpeg\n"
                "  Then:   ffmpeg -filters | grep subtitles"
            )
        path = op.subtitles.replace("\\", "/").replace(":", r"\:")
        style = f":force_style='{op.style}'" if op.style else ""
        self.video = self._step(f"subtitles='{path}'{style}", self.video, "v")

    def recolor(self, op: Recolor) -> None:
        """Apply the colour transfer, as a lookup table ffmpeg reads once.

        The table is regenerated here from the numbers in the plan rather than
        carried as a file, so a plan stays portable. Generating it is pure
        arithmetic over 4913 entries -- instant, and identical every time.

        CACHED IN THE XDG CACHE DIRECTORY, NOT THE SHARED TEMP DIRECTORY. The
        cube must still exist when ffmpeg actually reads it, which happens
        later -- after `compile_plan` has returned an argv, at whatever point
        the caller runs it (or not at all, for `--print-command`). That rules
        out a `tempfile.TemporaryDirectory()` scoped to this function: it
        would delete the file before ffmpeg ever opened it. So this is real,
        managed cache state, keyed by the numbers that determine its content,
        placed the same way `voices_dir()` places voice models -- not a
        temp file nobody owns, left behind in a directory the OS sweeps by
        accident rather than by design.
        """
        from vid.color import ColorStats, write_cube

        key = hashlib.sha256(
            repr((op.source_mean, op.source_std, op.reference_mean, op.reference_std, op.strength)).encode()
        ).hexdigest()[:16]
        cube = lut_cache_dir() / f"{key}.cube"
        if not cube.is_file():
            cube.parent.mkdir(parents=True, exist_ok=True)
            write_cube(
                ColorStats(mean=op.source_mean, std=op.source_std),
                ColorStats(mean=op.reference_mean, std=op.reference_std),
                cube,
                strength=op.strength,
            )
        path = str(cube).replace("\\", "/").replace(":", r"\:")
        self.video = self._step(f"lut3d='{path}'", self.video, "v")

    # ---- looks ----------------------------------------------------------
    #
    # ORDER IS THE CALLER'S, AND IT MATTERS. A vignette before a zoom is zoomed
    # INTO -- its dark corners get magnified away. After a zoom, it frames the
    # zoomed result. The plan is an ordered list and this just honours it, which
    # is the whole reason these are operations rather than flags on render.

    def vignette(self, op: Vignette) -> None:
        from vid.looks import vignette_filter

        self.video = self._step(vignette_filter(op.strength), self.video, "v")

    def grade(self, op: Grade) -> None:
        from vid.looks import resolve_look

        self.video = self._step(resolve_look(op.look), self.video, "v")

    def lut(self, op: Lut) -> None:
        if not Path(op.path).is_file():
            raise VidError(
                f"No such lookup table: {op.path!r}. Check the path is correct and relative to the current directory."
            )
        # A Windows drive-letter colon reads as an argument separator to the
        # filter parser, exactly as it does for subtitle paths.
        path = op.path.replace("\\", "/").replace(":", r"\:")
        self.video = self._step(f"lut3d='{path}'", self.video, "v")

    # ---- audio ----------------------------------------------------------
    #
    # These are the only operations that touch audio ALONE. Everything else --
    # trim, cut, retime, stitch -- carries audio alongside video automatically,
    # which was measured rather than assumed: a stitched crossfade really does
    # run acrossfade under the picture, and a cut really does keep the two
    # streams locked.

    def audio_remove(self, op: AudioRemove) -> None:
        """Mark the audio gone. A silent video is a normal thing to want.

        `None` rather than an empty stream, so the renderer omits the mapping
        entirely -- an empty audio stream and no audio stream are different
        files, and the second is what "remove" means.
        """
        self.audio = None
        # Silence a caller ASKED for, as distinct from a source that happened to
        # arrive without a track. A later stitch reads this: dropping a clip's
        # sound is carrying out a stated intent, where doing the same to an edit
        # that was only incidentally silent would be discarding audio nobody
        # said to discard.
        self.audio_removed = True

    def _extra_audio(self, track: str) -> str:
        """Add an audio file as an input and return its stream label."""
        self.inputs.append(track)
        return f"{len(self.inputs) - 1}:a"

    def audio_replace(self, op: AudioReplace) -> None:
        incoming = self._extra_audio(op.track)
        # apad then atrim: pad with silence if the track is short, cut it if
        # long. The result always matches the video, which is the answer every
        # caller wants and the one ffmpeg would otherwise decide by accident.
        chain = self._placed_audio(op.start)
        self.audio = self._step(chain, incoming, "a")

    def _placed_audio(self, start: float) -> str:
        if not math.isfinite(start) or start < 0:
            raise VidError("Audio start must be finite, nonnegative seconds.")
        if not math.isfinite(self.elapsed) or self.elapsed <= 0:
            raise VidError("Supplied audio needs a known positive video duration. Compile through `vid render`.")
        return (
            f"asetpts=PTS-STARTPTS,adelay={min(start, self.elapsed) * 1000:.6f}:all=1"
            f",apad,atrim=end={self.elapsed:.6f},asetpts=PTS-STARTPTS"
        )

    def audio_mix(self, op: AudioMix) -> None:
        if self.audio is None:
            raise VidError(
                "There is no audio left to mix into -- `audio remove` earlier in this "
                "chain took it away. Use `audio replace` to put a track on a silent video."
            )
        incoming = self._extra_audio(op.track)
        bed = self._step(
            f"volume={op.level}dB," + self._placed_audio(op.start),
            incoming,
            "a",
        )
        # Preserve a delayed source track on the picture timeline. Resetting
        # STARTPTS here advances its first sample instead of filling the gap.
        self._astep("aresample=async=1:first_pts=0" + self._bound_audio(self.elapsed))
        out = self._next("a")
        # duration=first keeps the result the length of the ORIGINAL audio, so a
        # long music file cannot quietly extend the video.
        # normalize=0 keeps the existing audio at its own level rather than
        # halving it to make room, which is what a caller means by "under".
        self.filters.append(f"[{self.audio}][{bed}]amix=inputs=2:duration=first:normalize=0[{out}]")
        self.audio = out


def _as_map_target(label: str) -> str:
    """Format a stream for `-map`, bracketing ONLY filter-graph pads.

    A filter output is `[v1]`. A raw input stream is `0:v` -- and bracketing that
    makes ffmpeg reject the whole command with "Invalid argument", which reads
    like a bad file rather than a bad flag.

    This could not happen until audio-only operations existed. Every verb before
    them put at least one filter on the video, so the video label was always a
    pad. `vid audio remove` leaves the picture untouched, so its label is still
    the raw input -- and the bug appeared the first time that was rendered.
    """
    return label if re.fullmatch(r"\d+:[vad]", label) else f"[{label}]"


#: ffmpeg's filtergraph mini-language puts every label a chain touches in a
#: bracket group at the very start (inputs) or the very end (outputs) of the
#: chain, never in the middle -- and every entry this compiler builds, by hand
#: or through `_step`, follows that shape. So the labels an already-built
#: entry consumes and produces can be read back out of the string it was
#: already turned into, rather than threaded through a second bookkeeping
#: path that every future operation would have to remember to update.
_LEADING_LABELS = re.compile(r"^(?:\[[^\]]+\])+")
_TRAILING_LABELS = re.compile(r"(?:\[[^\]]+\])+$")
_LABEL = re.compile(r"\[([^\]]+)\]")


def _entry_io(entry: str) -> tuple[list[str], list[str]]:
    """The input and output labels of one compiled filter-chain entry."""
    lead = _LEADING_LABELS.match(entry)
    inputs = _LABEL.findall(lead.group(0)) if lead else []
    rest = entry[lead.end() :] if lead else entry
    trail = _TRAILING_LABELS.search(rest)
    outputs = _LABEL.findall(trail.group(0)) if trail else []
    return inputs, outputs


def _seal_dangling_outputs(compiler: Compiler) -> None:
    """Sink any filter output nothing downstream ever reads.

    Every verb before this pass runs against the graph AS IT STANDS AT THAT
    MOMENT -- it has no way to know a later verb will throw its output away.
    `trim` puts audio alongside video because at the time it runs that is
    correct; if `audio remove` or `audio replace` follows, the audio label
    `trim` produced is simply never referenced again. ffmpeg does not treat an
    unconnected filter output as "dead code to prune" -- it refuses to build
    the graph at all ("has output N unconnected ... Invalid argument").

    So this runs once, after every operation has had its turn, over the
    FINISHED graph rather than the plan: for every label a filter produced,
    if nothing downstream consumes it and it is not one of the two labels
    actually mapped to output, it is routed to a null sink -- exactly the
    trick `zoom` already uses on its own reference-scaling branch (`nullsink`
    on `ref_out`). That keeps this general across any current or future verb
    pair with the same shape, instead of special-casing `audio remove` and
    `audio replace` against each verb that can precede them.
    """
    produced: list[str] = []
    seen: set[str] = set()
    consumed: set[str] = set()
    for entry in compiler.filters:
        inputs, outputs = _entry_io(entry)
        consumed.update(inputs)
        for label in outputs:
            if label not in seen:
                seen.add(label)
                produced.append(label)
    consumed.add(compiler.video)
    if compiler.audio is not None:
        consumed.add(compiler.audio)
    for label in produced:
        if label in consumed:
            continue
        sink = "nullsink" if label.startswith("v") else "anullsink"
        compiler.filters.append(f"[{label}]{sink}")


def compile_plan(
    plan: Plan,
    output: str,
    *,
    overwrite: bool = True,
    durations: dict[str, float] | None = None,
    has_audio: bool = True,
    frame_rate: float | None = None,
    dimensions: tuple[int, int] | None = None,
    source_audio: dict[str, bool] | None = None,
    source_sizes: dict[str, tuple[int, int]] | None = None,
    video_codec: str = "libx264",
) -> list[str]:
    """The whole plan as one ffmpeg argv."""
    validate_video_codec(plan, output, video_codec)
    if video_codec == "copy":
        total = (durations or {}).get(plan.source or "", 0.0)
        if not math.isfinite(total) or total <= 0:
            raise VidError("Picture stream-copy needs a known positive video duration. Compile through `vid render`.")
    compiler = Compiler(
        plan,
        durations=durations,
        has_audio=has_audio,
        frame_rate=frame_rate,
        dimensions=dimensions,
        source_audio=source_audio,
        source_sizes=source_sizes,
    )
    # A `match` on the operation's own class, not a dict of bound methods keyed
    # by name. The dict handed every handler the full `Operation` union rather
    # than its own concrete type, which is a real narrowing gap (13 diagnostics
    # from one line). Matching on the class narrows `operation` to its concrete
    # type in each case, so each handler receives exactly the type it declares.
    for operation in plan.operations:
        match operation:
            case Trim():
                compiler.trim(operation)
            case Cut():
                compiler.cut(operation)
            case Retime():
                compiler.retime(operation)
            case Zoom():
                compiler.zoom(operation)
            case Stitch():
                compiler.stitch(operation)
            case Overlay():
                compiler.overlay(operation)
            case Caption():
                compiler.caption(operation)
            case Recolor():
                compiler.recolor(operation)
            case Vignette():
                compiler.vignette(operation)
            case Grade():
                compiler.grade(operation)
            case Lut():
                compiler.lut(operation)
            case AudioRemove():
                compiler.audio_remove(operation)
            case AudioReplace():
                compiler.audio_replace(operation)
            case AudioMix():
                compiler.audio_mix(operation)
            case _:
                raise VidError(
                    f"This version of vid cannot compile a {operation.op!r} operation. "
                    "The plan may have been written by a newer version -- upgrade vid, or "
                    "rebuild the plan with this version's verbs."
                )

    _seal_dangling_outputs(compiler)

    command = ["ffmpeg"]
    if overwrite:
        command.append("-y")
    for source in compiler.inputs:
        command += ["-i", source]
    if compiler.filters:
        command += ["-filter_complex", ";".join(compiler.filters)]
    if compiler.filters or video_codec == "copy":
        command += ["-map", _as_map_target(compiler.video)]
        if compiler.audio is not None:
            command += ["-map", _as_map_target(compiler.audio)]
    command += ["-c:v", video_codec]
    if video_codec != "copy":
        command += ["-preset", "medium", "-pix_fmt", "yuv420p"]
    if compiler.audio is not None:
        command += ["-c:a", "aac"]
        # NO -shortest HERE, and that was learned the hard way. It looked like
        # exactly the right tool -- the video lands correctly and only atempo's
        # rounding runs long -- and it made 2x and 1.5x exact. It also broke
        # every SLOW-DOWN: at 0.5x, atempo's output is slightly SHORT, so
        # -shortest truncated a 6.000s edit to 5.986s. A flag that takes the
        # shorter of two streams cannot fix an error that changes sign.
        #
        # The audio is bounded in `retime` itself instead, where the intended
        # duration is actually known.
    else:
        # Explicit, not implied. `-an` states the intent in the command a
        # caller can read with --print-command.
        command.append("-an")
    command.append(output)
    return command


def validate_video_codec(plan: Plan, output: str, video_codec: str) -> None:
    """Refuse copy rather than silently discarding a picture edit."""
    if video_codec not in {"libx264", "copy"}:
        raise VidError("video_codec must be 'libx264' or 'copy'.")
    if video_codec != "copy":
        return
    if any(not isinstance(op, (AudioRemove, AudioReplace, AudioMix)) for op in plan.operations):
        raise VidError("Picture stream-copy only supports audio-only plans. Use libx264 for picture or timing edits.")
    source_suffix = Path(plan.source or "").suffix.lower()
    if source_suffix not in {".mp4", ".mov"} or Path(output).suffix.lower() != source_suffix:
        raise VidError(
            "Picture stream-copy requires the same MP4 or MOV container extension as the source. "
            "Other containers or conversions are not qualified for packet preservation; use libx264."
        )
