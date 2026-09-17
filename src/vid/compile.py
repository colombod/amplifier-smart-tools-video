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

import re
from pathlib import Path

from vid.plan import (
    AudioMix,
    AudioRemove,
    AudioReplace,
    Caption,
    Cut,
    Plan,
    Grade,
    Lut,
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

#: A slow zoom moves `zoompan`'s crop by a fraction of a source pixel per frame,
#: and the crop quantises to whole pixels -- so the motion alternates 1,1,2,1,1,2
#: and visibly stutters. Upscaling first makes each rounding error a fraction of
#: an output pixel. The factor is folklore, but the jitter is real and this is
#: the accepted fix.
ZOOM_UPSCALE = 4

#: How finely a ramp between two control points is subdivided. Each piece costs a
#: trim plus an atempo chain in the graph, so this trades filter-graph size
#: against how closely the curve is followed.
RAMP_STEPS = 8


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
    for first, second in zip(points, points[1:], strict=False):
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

    def cut(self, op: Cut) -> None:
        """Remove a range by keeping what is either side of it and rejoining."""
        head_v = self._step(f"trim=start=0:end={op.start},setpts=PTS-STARTPTS", self.video, "v")
        tail_v = self._step(f"trim=start={op.end},setpts=PTS-STARTPTS", self.video, "v")
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

    def retime(self, op: Retime) -> None:
        if op.speed is not None:
            self.video = self._step(f"setpts={1 / op.speed:.6f}*PTS", self.video, "v")
            self._astep(",".join(atempo_chain(op.speed)))
            return

        segments = ramp_segments(op.ramp)
        parts: list[str] = []
        for start, end, speed in segments:
            seg_v = self._step(
                f"trim=start={start}:end={end},setpts=PTS-STARTPTS,setpts={1 / speed:.6f}*PTS",
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

    def zoom(self, op: Zoom) -> None:
        frames = max(1, int(op.duration * 30))
        step = (op.to - 1.0) / frames
        self.video = self._step(
            f"scale=iw*{ZOOM_UPSCALE}:-2,"
            f"zoompan=z='min(zoom+{step:.6f},{op.to})':d={frames}"
            f":x='{op.x}':y='{op.y}':fps=30,"
            f"scale=iw/{ZOOM_UPSCALE}:-2",
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
            if op.transition:
                self._transition(other_v, other_a, op)
                self.elapsed += self.durations.get(source, 0.0) - op.transition_duration
            else:
                self.elapsed += self.durations.get(source, 0.0)
                out_v, out_a = self._next("v"), self._next("a")
                self.filters.append(
                    f"[{self.video}][{self.audio}][{other_v}][{other_a}]concat=n=2:v=1:a=1[{out_v}][{out_a}]"
                )
                self.video, self.audio = out_v, out_a

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
                    "Custom transitions are generated and verified when the plan is built."
                )
            if not op.transition_verified:
                raise VidError(
                    "This plan carries an UNVERIFIED custom transition. A generated "
                    "expression reaches a plan only after being proven to blend against "
                    "the clips it will be used on -- refusing rather than rendering "
                    "something whose behaviour nobody checked."
                )
            expr = f":expr='{op.transition_expr}'"
        self.filters.append(
            f"[{self.video}][{other_v}]"
            f"xfade=transition={op.transition}:duration={op.transition_duration}:offset={offset}"
            f"{expr}[{out_v}]"
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
        """
        import tempfile

        from vid.color import ColorStats, write_cube

        cube = Path(tempfile.gettempdir()) / f"vid-lut-{abs(hash((op.source_mean, op.reference_mean, op.strength)))}.cube"
        if not cube.is_file():
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
            raise VidError(f"No such lookup table: {op.path!r}")
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

    def _extra_audio(self, track: str) -> str:
        """Add an audio file as an input and return its stream label."""
        self.inputs.append(track)
        return f"{len(self.inputs) - 1}:a"

    def audio_replace(self, op: AudioReplace) -> None:
        incoming = self._extra_audio(op.track)
        # apad then atrim: pad with silence if the track is short, cut it if
        # long. The result always matches the video, which is the answer every
        # caller wants and the one ffmpeg would otherwise decide by accident.
        total = self.elapsed or None
        chain = "apad" + (f",atrim=end={total},asetpts=PTS-STARTPTS" if total else "")
        self.audio = self._step(chain, incoming, "a")

    def audio_mix(self, op: AudioMix) -> None:
        if self.audio is None:
            raise VidError(
                "There is no audio left to mix into -- `audio remove` earlier in this "
                "chain took it away. Use `audio replace` to put a track on a silent video."
            )
        incoming = self._extra_audio(op.track)
        total = self.elapsed or None
        bed = self._step(
            f"volume={op.level}dB,apad" + (f",atrim=end={total},asetpts=PTS-STARTPTS" if total else ""),
            incoming,
            "a",
        )
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


def compile_plan(
    plan: Plan,
    output: str,
    *,
    overwrite: bool = True,
    durations: dict[str, float] | None = None,
    has_audio: bool = True,
) -> list[str]:
    """The whole plan as one ffmpeg argv."""
    compiler = Compiler(plan, durations=durations, has_audio=has_audio)
    dispatch = {
        "trim": compiler.trim,
        "cut": compiler.cut,
        "retime": compiler.retime,
        "zoom": compiler.zoom,
        "stitch": compiler.stitch,
        "caption": compiler.caption,
        "recolor": compiler.recolor,
        "vignette": compiler.vignette,
        "grade": compiler.grade,
        "lut": compiler.lut,
        "audio_remove": compiler.audio_remove,
        "audio_replace": compiler.audio_replace,
        "audio_mix": compiler.audio_mix,
    }
    for operation in plan.operations:
        handler = dispatch.get(operation.op)
        if handler is None:
            raise VidError(f"This version of vid cannot compile a {operation.op!r} operation.")
        handler(operation)

    command = ["ffmpeg"]
    if overwrite:
        command.append("-y")
    for source in compiler.inputs:
        command += ["-i", source]
    if compiler.filters:
        command += ["-filter_complex", ";".join(compiler.filters)]
        command += ["-map", _as_map_target(compiler.video)]
        if compiler.audio is not None:
            command += ["-map", _as_map_target(compiler.audio)]
    command += ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p"]
    if compiler.audio is not None:
        command += ["-c:a", "aac"]
    else:
        # Explicit, not implied. `-an` states the intent in the command a
        # caller can read with --print-command.
        command.append("-an")
    command.append(output)
    return command
