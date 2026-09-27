"""Missing ffmpeg/ffprobe must surface as a named VidError, never a bare
`FileNotFoundError` traceback -- and a partial `describe` pass must never
leave a shot indistinguishable from one nobody tried to describe.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.fixtures import ensure_clips, ensure_undecodable_clip, have_ffmpeg
from vid.index import _duration, build, describe, detect_shots, index_dir, index_path, load
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="these run real ffmpeg/ffprobe")


def test_load_with_a_corrupt_index_raises_a_named_vid_error_not_a_traceback(monkeypatch, tmp_path):
    """`cli.main` only translates `VidError` into a named, non-zero exit --
    everything else escapes as a bare traceback. A corrupt or truncated index
    file used to reach `json.loads` unguarded and do exactly that."""
    monkeypatch.setenv("VID_INDEX_DIR", str(tmp_path / "index-store"))
    video = str(tmp_path / "clip.mp4")
    Path(video).write_bytes(b"stand-in bytes -- fingerprint() only needs a real file to hash")

    path = index_path(video)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json", encoding="utf-8")

    with pytest.raises(VidError) as failure:
        load(video)

    message = str(failure.value)
    assert str(path) in message, "the failure must name WHICH index file is corrupt"
    assert f"vid index {video}" in message, "the failure must name the remedy: rebuild it"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits this test sets")
def test_load_from_an_unreadable_index_dir_names_the_setting_not_a_traceback(monkeypatch, tmp_path):
    """THE THIRD SIBLING of a class already fixed twice on this branch.

    `save` names VID_INDEX_DIR when a write is refused, and the corrupt-JSON
    case above names the file and its remedy. The READ path's two filesystem
    calls were bare, so an unreadable index directory escaped `is_file` as:

        PermissionError: [Errno 13] Permission denied: '.../bf6781c471cbcf0d.json'

    `cli.main` translates only `VidError`, so that reached the user as a stack
    trace naming neither the setting that chose the location nor a remedy.
    """
    store = tmp_path / "index-store"
    store.mkdir()
    monkeypatch.setenv("VID_INDEX_DIR", str(store))
    video = str(tmp_path / "clip.mp4")
    Path(video).write_bytes(b"stand-in bytes -- fingerprint() only needs a real file to hash")

    store.chmod(0o000)
    try:
        with pytest.raises(VidError) as failure:
            load(video)
    finally:
        store.chmod(0o700)

    message = str(failure.value)
    assert "VID_INDEX_DIR" in message, "the failure must name the setting that chose the location"
    assert "Permission denied" in message, f"the failure must carry the real cause: {message!r}"


def test_an_unreadable_index_is_translated_even_where_chmod_does_not_bite(monkeypatch, tmp_path):
    """The same guard, provable in CI, which runs as root and ignores chmod.

    THE SUBSTITUTION IS THE OS REFUSAL AND NOTHING ELSE. The test above is the
    real thing -- a real chmod 000, a real `PermissionError` out of a real
    `stat` -- and it is what establishes that this is the error the filesystem
    actually raises. It cannot run as root, so it is skipped in the container,
    and a guard whose only test never executes in CI is a guard nobody is
    watching.

    So this one raises that exact exception from `is_file` and checks only the
    translation. It asserts nothing about what the OS does; the test above
    already measured that.
    """
    monkeypatch.setenv("VID_INDEX_DIR", str(tmp_path / "index-store"))
    video = str(tmp_path / "clip.mp4")
    Path(video).write_bytes(b"stand-in bytes -- fingerprint() only needs a real file to hash")

    # SCOPED TO THE INDEX FILE. A blanket refusal also hits `fingerprint`,
    # which reads the VIDEO -- a different site, with a different remedy, and
    # this test would then pass on the wrong guard. (That blanket version is
    # what exposed `fingerprint`'s own unguarded `is_file` in the first place;
    # it has its own test below.)
    real_is_file = Path.is_file

    def refuse(self, *args, **kwargs):
        if self.suffix == ".json":
            raise PermissionError(13, "Permission denied")
        return real_is_file(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", refuse)

    with pytest.raises(VidError) as failure:
        load(video)

    message = str(failure.value)
    assert "VID_INDEX_DIR" in message
    assert "Permission denied" in message


def test_an_unreadable_video_is_named_rather_than_called_missing(monkeypatch, tmp_path):
    """`fingerprint` reads the VIDEO, and had the same bare `is_file`.

    Found by the blanket monkeypatch the test above now scopes: refusing every
    `is_file` surfaced this fourth site. "No such video" would be the wrong
    sentence here -- the file exists, it cannot be read -- and unguarded it
    escaped as a raw PermissionError traceback instead.
    """
    monkeypatch.setenv("VID_INDEX_DIR", str(tmp_path / "index-store"))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"stand-in bytes")

    def refuse(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "is_file", refuse)

    with pytest.raises(VidError) as failure:
        load(str(video))

    message = str(failure.value)
    assert "could not be read" in message, f"an unreadable video must not be reported as missing: {message!r}"
    assert "No such video" not in message, f"an unreadable video must not be reported as missing: {message!r}"
    assert "Permission denied" in message


def test_a_video_that_refuses_to_open_is_named_too(monkeypatch, tmp_path):
    """`fingerprint` touches the filesystem three times, not once.

    ADDED BECAUSE MUTATION TESTING CAUGHT IT: breaking the `stat`/`open` half
    of that guard left the whole suite green, because the test above refuses at
    `is_file` and never reaches the read. A file whose DIRECTORY is traversable
    but which is itself unreadable takes exactly that second path.
    """
    monkeypatch.setenv("VID_INDEX_DIR", str(tmp_path / "index-store"))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"stand-in bytes")

    def refuse(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", refuse)

    with pytest.raises(VidError) as failure:
        load(str(video))

    message = str(failure.value)
    assert "could not be read" in message, f"an unopenable video must be named, not raw: {message!r}"
    assert "Permission denied" in message


def _scrub_path(monkeypatch, tmp_path) -> None:
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))


def test_duration_with_no_ffmpeg_raises_a_named_error_not_a_traceback(monkeypatch, tmp_path):
    """Before the preflight, this reached `subprocess.run(["ffprobe", ...])`
    directly and a missing binary escaped as a bare `FileNotFoundError` --
    invisible to `cli.main`'s `except VidError` boundary."""
    _scrub_path(monkeypatch, tmp_path)

    with pytest.raises(VidError) as failure:
        _duration("whatever.mp4")

    message = str(failure.value)
    assert "ffmpeg" in message.lower()
    assert "vid check" in message


def test_detect_shots_with_no_ffmpeg_raises_a_named_error_not_a_traceback(monkeypatch, tmp_path):
    _scrub_path(monkeypatch, tmp_path)

    with pytest.raises(VidError) as failure:
        detect_shots("whatever.mp4")

    message = str(failure.value)
    assert "ffmpeg" in message.lower()
    assert "vid check" in message


class _SkippingIntelligence:
    """Describes every frame it was shown except any shot id in `skip` --
    reads the real prompt rather than returning a canned reply."""

    def __init__(self, skip: frozenset[str]) -> None:
        self.skip = skip

    def run(self, request):
        lines = []
        for line in request.prompt.splitlines():
            if " -- the shot at " not in line:
                continue
            name, _, shot_id = line.partition(" -- the shot at ")
            shot_id = shot_id.strip()
            if shot_id in self.skip:
                continue
            lines.append(f"{name}: a plain frame.")
        return type("Result", (), {"text": "\n".join(lines), "error": None})()


def test_describe_refuses_a_partial_result_and_leaves_the_record_unchanged():
    """A model that answers about only some of the pending shots must not
    have its partial answer applied -- the caller gets a named failure, and
    every shot stays exactly as distinguishable (pending) as it was before."""
    video = str(ensure_clips()["alpha"].path)
    record = {
        "shots": [
            {"id": "s0", "start": 0.5, "end": 1.0},
            {"id": "s1", "start": 1.5, "end": 2.0},
        ]
    }

    with pytest.raises(VidError) as failure:
        describe(video, record, _SkippingIntelligence(skip=frozenset({"s1"})))

    message = str(failure.value)
    assert "s1" in message
    assert all("description" not in shot for shot in record["shots"]), (
        "a refused partial result must not be written to the record at all"
    )


def test_describe_applies_every_shot_when_the_model_answers_for_all():
    video = str(ensure_clips()["alpha"].path)
    record = {
        "shots": [
            {"id": "s0", "start": 0.5, "end": 1.0},
            {"id": "s1", "start": 1.5, "end": 2.0},
        ]
    }

    updated = describe(video, record, _SkippingIntelligence(skip=frozenset()))

    assert all(shot.get("description") for shot in updated["shots"])


# ---------------------------------------------------------------------------
# Deviation "partial-results-are-failures": `detect_shots` never checked
# ffmpeg's own exit code. A file whose container metadata is intact but whose
# frame data ffmpeg cannot decode used to fall through to the `[0.0, duration]`
# default -- one shot spanning the whole video, indistinguishable from a real
# single-shot result. `build()` must not write an index built on that default.
# ---------------------------------------------------------------------------


def test_detect_shots_when_ffmpeg_cannot_decode_the_video_raises_a_named_error():
    video = str(ensure_undecodable_clip())

    with pytest.raises(VidError) as failure:
        detect_shots(video)

    message = str(failure.value)
    assert "ffmpeg exited" in message
    assert "single shot spanning the whole video" in message


def test_build_leaves_no_index_file_when_scene_detection_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("VID_INDEX_DIR", str(tmp_path / "index-store"))
    video = str(ensure_undecodable_clip())

    with pytest.raises(VidError):
        build(video, speech=False)

    assert not index_path(video).is_file(), (
        "a scene-detection failure left an index file behind claiming one whole-video shot"
    )


# ---------------------------------------------------------------------------
# Deviation "artifact-location-named": a relative VID_INDEX_DIR used to be
# handed straight to callers, who could only resolve the reported index path
# correctly from the one cwd this process happened to run in.
# ---------------------------------------------------------------------------


def test_index_dir_resolves_a_relative_override_to_an_absolute_path(monkeypatch, tmp_path):
    workdir = tmp_path / "somewhere"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("VID_INDEX_DIR", "relative-index-store")

    resolved = index_dir()

    assert resolved.is_absolute()
    assert resolved == (workdir / "relative-index-store").resolve()
