"""Recolor's generated LUT must live in managed, per-user cache state -- never
an unmanaged file left behind in the shared temp directory.

Pure arithmetic and JSON throughout, so none of this needs ffmpeg installed.
"""

from __future__ import annotations

from vid.compile import compile_plan, lut_cache_dir
from vid.plan import Plan, Recolor


def _recolor_plan() -> Plan:
    return Plan(source="x.mp4").with_operation(
        Recolor(
            reference="ref.png",
            source_mean=(10.0, 2.0, 3.0),
            source_std=(4.0, 5.0, 6.0),
            reference_mean=(70.0, 8.0, 9.0),
            reference_std=(10.0, 11.0, 12.0),
            strength=0.7,
        )
    )


def test_the_generated_lut_lands_in_the_configured_cache_dir_not_the_temp_dir(monkeypatch, tmp_path):
    cache_dir = tmp_path / "lut-cache"
    monkeypatch.setenv("VID_LUT_CACHE_DIR", str(cache_dir))

    command = compile_plan(_recolor_plan(), "out.mp4", has_audio=False)

    assert lut_cache_dir() == cache_dir
    cubes = list(cache_dir.glob("*.cube"))
    assert len(cubes) == 1, "the generated LUT must be written under the configured cache directory"
    # The filter graph escapes ":" as "\:"; undo that before checking the path is referenced.
    argv = " ".join(command).replace(r"\:", ":")
    assert str(cubes[0]) in argv


def test_recompiling_the_same_recolor_reuses_the_cached_lut(monkeypatch, tmp_path):
    cache_dir = tmp_path / "lut-cache"
    monkeypatch.setenv("VID_LUT_CACHE_DIR", str(cache_dir))

    compile_plan(_recolor_plan(), "out1.mp4", has_audio=False)
    compile_plan(_recolor_plan(), "out2.mp4", has_audio=False)

    cubes = list(cache_dir.glob("*.cube"))
    assert len(cubes) == 1, "identical recolor parameters must reuse one cached LUT, not multiply files"


def test_a_different_recolor_gets_its_own_cached_lut(monkeypatch, tmp_path):
    cache_dir = tmp_path / "lut-cache"
    monkeypatch.setenv("VID_LUT_CACHE_DIR", str(cache_dir))

    compile_plan(_recolor_plan(), "out1.mp4", has_audio=False)
    other = Plan(source="x.mp4").with_operation(
        Recolor(
            reference="ref.png",
            source_mean=(1.0, 1.0, 1.0),
            source_std=(1.0, 1.0, 1.0),
            reference_mean=(2.0, 2.0, 2.0),
            reference_std=(2.0, 2.0, 2.0),
            strength=0.3,
        )
    )
    compile_plan(other, "out2.mp4", has_audio=False)

    cubes = list(cache_dir.glob("*.cube"))
    assert len(cubes) == 2, "distinct recolor parameters must not collide on one cached LUT"
