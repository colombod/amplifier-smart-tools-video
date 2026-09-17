"""Experiment 5x2: can a model write an xfade `custom` expression that works?

The model here is Claude (me), generating expressions from plain-language
descriptions. That is a material fact about this evidence and it is recorded
rather than glossed: this did NOT go through the tool's configured intelligence
backend, so it measures what a capable model can produce, not what the shipped
path would produce.

Judgment is NOT mine. Every expression is rendered and then graded by the
deterministic property checks in vid.verify. The model generates; arithmetic
decides.
"""

import json
import subprocess
import sys

sys.path.insert(0, "/mnt/linuxdata/workspaces/hackathon-smart-tools/amplifier-smart-tools-video")
sys.path.insert(0, "/mnt/linuxdata/workspaces/hackathon-smart-tools/amplifier-smart-tools-video/src")
from vid import verify as checks

A = "tests/fixtures/alpha.mp4"  # red, 3s
B = "tests/fixtures/bravo.mp4"  # green, 3s
DUR, OFFSET = 1.0, 2.0  # 1s transition starting at 2.0s

# Description -> the expression I produced for it, acting as the model.
CASES = [
    ("a plain crossfade", "A*(1-P)+B*P"),
    ("a hard edge sweeping in from the right", "if(gt(X,W*(1-P)),B,A)"),
    ("fade down to black, then up into the second clip", "if(lt(P,0.5), A*(1-2*P), B*(2*P-1))"),
    ("the second clip opens in a circle growing from the centre", "if(lt(hypot(X-W/2,Y-H/2), P*hypot(W/2,H/2)), B, A)"),
    ("a diagonal wipe from the top-left corner", "if(lt((X/W+Y/H)/2, P), B, A)"),
    (
        "the second clip slams in from the right, overshooting slightly before settling",
        "if(gt(X, W*(1-min(1,P*1.3))), B, A)",
    ),
]

results = []
for i, (description, expr) in enumerate(CASES):
    out = f"/tmp/gen/g{i}.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        A,
        "-i",
        B,
        "-filter_complex",
        f"[0:v][1:v]xfade=transition=custom:duration={DUR}:offset={OFFSET}:expr='{expr}'[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        out,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.returncode != 0:
        results.append(
            (
                description,
                expr,
                "WILL-NOT-PARSE",
                proc.stderr.strip().splitlines()[-1][:90] if proc.stderr.strip() else "ffmpeg failed",
            )
        )
        continue

    # Deterministic grading. Mid-transition must resemble NEITHER side.
    mid = OFFSET + DUR / 2
    blend = checks.check_transition_at(out, mid, window=DUR / 2 + 0.3)
    dur = checks.check_duration(out, 3.0 + 3.0 - DUR, tolerance=0.2)

    if blend.held and dur.held:
        verdict, note = "CORRECT", blend.measured
    else:
        bad = [c for c in (blend, dur) if not c.held]
        verdict = "VALID-BUT-WRONG"
        note = "; ".join(f"{c.name}: {c.measured} {c.note}".strip() for c in bad)
    results.append((description, expr, verdict, note))

print(f"{'VERDICT':<17} {'DESCRIPTION'}")
print("-" * 100)
for description, expr, verdict, note in results:
    print(f"{verdict:<17} {description}")
    print(f"{'':<17}   expr: {expr}")
    print(f"{'':<17}   {note}")
    print()
tally = {}
for *_, v, _ in [(d, e, v, n) for d, e, v, n in results]:
    tally[v] = tally.get(v, 0) + 1
print("TALLY:", json.dumps(tally))
