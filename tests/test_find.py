"""`find`, against a talk whose content and timing we authored.

The fixture's segment boundaries are known, so "did it find the right passage?"
has an exact answer rather than an impression.

ON TOLERANCE, because the first version of these tests was wrong. Demanding ZERO
overlap with a neighbouring topic failed -- and the failure was the test's, not
the tool's. The transcriber emits chunks on its own boundaries (whole seconds
here) which do not align with the TTS segment edges at 10.8s and 21.5s, so a hit
at a topic seam necessarily bleeds a fraction of a second into its neighbour.
Measured, that bleed is 4.7% and 6.5% of the hit while the intended topic covers
95.3% and 93.5%. So the honest assertion is that the intended segment DOMINATES,
not that the neighbour is untouched.
"""

from __future__ import annotations

import pytest

from tests.speech_fixtures import ensure_talk, have_ffmpeg, have_tts
from vid.find import find, find_described, find_literal
from vid.index import Chunk, build, chunks_of, load
from vid.schemas import VidError

pytestmark = pytest.mark.skipif(not (have_tts() and have_ffmpeg()), reason="speech fixtures need espeak-ng and ffmpeg")


@pytest.fixture(scope="module")
def talk(tmp_path_factory, monkeypatch_session=None):
    import os

    os.environ.setdefault("VID_INDEX_DIR", str(tmp_path_factory.mktemp("index")))
    path, segments = ensure_talk()
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        pytest.skip("no speech backend installed")
    record = load(str(path)) or build(str(path))
    return record, {s.topic: (s.start, s.end) for s in segments}


def _overlap(hit, span) -> float:
    lo, hi = span
    return max(0.0, min(hit.end, hi) - max(hit.start, lo))


@pytest.mark.parametrize(
    "query,topic",
    [
        ("pricing plan cost dollars", "pricing"),
        ("message queue workers", "architecture"),
        ("support ticket", "support"),
    ],
)
def test_a_literal_search_lands_in_the_authored_segment(talk, query, topic):
    record, truth = talk
    hit = find(chunks_of(record), query)[0]

    length = hit.end - hit.start
    intended = _overlap(hit, truth[topic]) / length
    assert intended > 0.85, f"{query!r} landed mostly outside {topic}: {intended:.0%}"

    for other, span in truth.items():
        if other == topic:
            continue
        assert _overlap(hit, span) / length < 0.15, (
            f"{query!r} bled {_overlap(hit, span):.2f}s into {other} -- more than a seam artifact"
        )


def test_matches_in_different_topics_do_not_merge_into_one_span(talk):
    """The bug authored ground truth caught.

    "plan" appears in the pricing passage AND in "customers on any plan can open
    a ticket", two topics apart. Merging every match into one span reported
    11.0-29.0s -- a range straddling pricing, support, and a passage between them
    that matched nothing at all.
    """
    record, truth = talk
    hits = find(chunks_of(record), "pricing plan cost dollars")

    assert len(hits) >= 2, "the isolated match should be its own run, not folded into the first"
    best = hits[0]
    assert best.end <= truth["support"][0] + 0.6, (
        f"the best hit runs to {best.end}s, past the end of pricing -- the runs are merging again"
    )


def test_an_id_the_index_does_not_contain_is_refused_not_interpreted(talk):
    """The one wrong answer this shape permits, and it must not pass silently.

    A model cannot return a wrong TIME here -- it is never asked for one. The
    worst it can do is name a passage that does not exist, which is a membership
    test away from being caught.
    """
    record, _ = talk
    chunks = chunks_of(record)

    class Inventing:
        def run(self, request):
            return type("R", (), {"text": "c0 c99\nthe pricing is in these", "error": None})()

    with pytest.raises(VidError, match="not in this video's index"):
        find_described(chunks, "pricing", Inventing())


def test_a_reply_with_no_ids_at_all_is_refused(talk):
    record, _ = talk

    class Waffling:
        def run(self, request):
            return type("R", (), {"text": "It appears around the middle of the video.", "error": None})()

    with pytest.raises(VidError, match="no passage ids"):
        find_described(chunks_of(record), "pricing", Waffling())


def test_a_described_search_with_no_model_refuses_rather_than_guessing(talk):
    """Honest degradation: say a model is needed, do not return something plausible."""
    record, _ = talk
    with pytest.raises(VidError, match="not configured"):
        find(chunks_of(record), "the bit about how much it sets you back", intelligence=None)


def test_a_query_of_only_common_words_is_refused():
    chunks = [Chunk(id="c0", start=0.0, end=1.0, text="hello")]
    with pytest.raises(VidError, match="nothing searchable"):
        find_literal(chunks, "where is the part about it")


def test_an_unindexed_video_says_to_index_it():
    with pytest.raises(VidError, match="vid index"):
        find([], "pricing")


def test_every_hit_carries_the_evidence_that_produced_it(talk):
    """A range with no text beside it is a claim a caller cannot check."""
    record, _ = talk
    hit = find(chunks_of(record), "pricing plan cost dollars")[0]
    assert hit.chunk_ids and hit.text
    assert hit.how == "literal"
    assert "dollar" in hit.text.lower() or "$" in hit.text
