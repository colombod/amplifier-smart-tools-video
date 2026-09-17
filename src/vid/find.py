"""Finding a moment by what was said.

THE GUARD, stated once and enforced structurally: a model is never asked for a
timestamp. The index holds chunks that already carry times the TRANSCRIBER
produced. A model is shown those chunks and asked which ones, BY ID. The time is
then a dictionary lookup.

This is not a discipline we maintain; it is a shape that makes the failure
unreachable. A model cannot return a wrong time here because it is never in a
position to return a time at all. The worst it can do is name an id that does not
exist, and that is a membership test away from being caught.

Two tiers, matching the rest of the tool:

  literal    words that appear in the transcript      no model, no provider, $0.00
  described  a phrase a model matches to chunks       bounded by the chunk list
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from vid.index import Chunk
from vid.schemas import VidError

#: Words too common to carry meaning in a search. Kept small on purpose -- an
#: aggressive stop list silently drops the one word that mattered.
_NOISE = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "at",
    "it",
    "that",
    "this",
    "where",
    "when",
    "does",
    "do",
    "did",
    "she",
    "he",
    "they",
    "about",
    "talk",
    "talks",
    "discussed",
    "discuss",
    "explains",
    "explain",
    "mention",
    "mentions",
    "for",
    "with",
    "part",
    "section",
    "bit",
}


@dataclass(frozen=True)
class Hit:
    """One matched region, and the evidence that produced it."""

    start: float
    end: float
    chunk_ids: list[str]
    text: str
    how: str
    rationale: str | None = None


def visual_chunks(record: dict) -> list[Chunk]:
    """Shot descriptions, as searchable passages.

    Deliberately the same Chunk type as speech: a hit is a time range with the
    evidence that produced it, and whether that evidence was heard or seen does
    not change its shape -- only its label.
    """
    return [
        Chunk(id=shot["id"], start=shot["start"], end=shot["end"], text=shot["description"])
        for shot in record.get("shots", [])
        if shot.get("description")
    ]


def _terms(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", query.lower())
    return [w for w in words if w not in _NOISE and len(w) > 2]


def find_literal(chunks: list[Chunk], query: str) -> list[Hit]:
    """Chunks whose text actually contains the query's words.

    Deterministic, instant, free, and correct whenever the caller happens to use
    the speaker's vocabulary. It is tried FIRST so that a caller who knows what
    was said never pays a model to tell them.
    """
    terms = _terms(query)
    if not terms:
        raise VidError(
            f"There is nothing searchable in {query!r} -- it is all common words. "
            "Name something that would actually have been said."
        )

    scored: list[tuple[int, Chunk]] = []
    for chunk in chunks:
        haystack = chunk.text.lower()
        score = sum(1 for term in terms if term in haystack)
        if score:
            scored.append((score, chunk))
    if not scored:
        return []

    hits = _runs(scored, chunks, how="literal")

    # A WEAK MATCH IS WORSE THAN NO MATCH, and this was found by a test that
    # failed for the wrong reason. Searching for "the bit about how much it sets
    # you back" matched the single word "how" -- in "let us look at HOW the system
    # is put together" -- and returned the architecture passage, confidently, when
    # the right answer was pricing. One incidental word beat escalating to a model
    # that would have understood the phrase.
    #
    # So a literal hit has to carry real evidence: at least half the query's
    # meaningful words. Below that the caller is not using the speaker's
    # vocabulary, which is exactly the case the described tier exists for.
    best = hits[0]
    covered = sum(1 for term in terms if term in best.text.lower())
    if covered / len(terms) < 0.5:
        return []

    return hits


def _runs(
    scored: list[tuple[int, Chunk]], all_chunks: list[Chunk], *, how: str, rationale: str | None = None
) -> list[Hit]:
    """Group matches into CONTIGUOUS runs, best first.

    Merging every match into one span was wrong, and testing against authored
    ground truth is what caught it. Searching a talk for "pricing plan cost
    dollars" matched c3, c4, c5 -- the pricing passage -- and also c7, which says
    "customers on any plan can open a ticket". One span from c3 to c7 covers c6
    as well, which matched nothing, and reports a range straddling two topics.

    A caller asking where pricing is discussed wants the pricing passage, not the
    smallest interval containing every incidental word match. So runs are kept
    separate and ranked; the best one is the answer, and the others are still
    there for a caller who wants them.
    """
    position = {chunk.id: i for i, chunk in enumerate(all_chunks)}
    by_score = {chunk.id: score for score, chunk in scored}
    matched = sorted((chunk for _, chunk in scored), key=lambda c: position[c.id])

    groups: list[list[Chunk]] = []
    for chunk in matched:
        if groups and position[chunk.id] == position[groups[-1][-1].id] + 1:
            groups[-1].append(chunk)
        else:
            groups.append([chunk])

    hits = [(sum(by_score[c.id] for c in group), _merge(group, how=how, rationale=rationale)) for group in groups]
    # Total score first, then length: a run of three weak matches beats one strong
    # isolated word, which is what "this passage is ABOUT the thing" looks like.
    hits.sort(key=lambda pair: (pair[0], len(pair[1].chunk_ids)), reverse=True)
    return [hit for _, hit in hits]


def _merge(chunks: list[Chunk], *, how: str, rationale: str | None = None) -> Hit:
    """Adjacent chunks become one region. A caller wants a range, not a list."""
    ordered = sorted(chunks, key=lambda c: c.start)
    return Hit(
        start=ordered[0].start,
        end=ordered[-1].end,
        chunk_ids=[c.id for c in ordered],
        text=" ".join(c.text for c in ordered),
        how=how,
        rationale=rationale,
    )


def find_described(chunks: list[Chunk], description: str, intelligence) -> list[Hit]:
    """Ask a model WHICH CHUNKS, never WHEN.

    The reply is a list of ids. Any id not in the index is refused rather than
    interpreted, because an invented id is the only wrong answer this shape
    permits and it must not pass silently.
    """
    from vid.intelligence import ask

    listing = "\n".join(f"{c.id}: {c.text}" for c in chunks)
    reply = ask(
        intelligence,
        (
            "Below are numbered passages from a video's transcript. Identify which "
            "passages match the request.\n\n"
            f"REQUEST: {description}\n\n"
            f"PASSAGES:\n{listing}\n\n"
            "Reply with the matching ids separated by spaces on the first line, and "
            "one short sentence of reasoning on the second. Nothing else. Use only "
            "ids from the list above. If nothing matches, reply NONE."
        ),
    )

    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    first = lines[0]
    rationale = lines[1] if len(lines) > 1 else None
    if first.upper().startswith("NONE"):
        return []

    known = {chunk.id: chunk for chunk in chunks}
    wanted = re.findall(r"\bc\d+\b", first)
    if not wanted:
        raise VidError(f"The model answered {first!r}, which contains no passage ids. Expected ids like `c3 c4`.")

    unknown = [i for i in wanted if i not in known]
    if unknown:
        # The one wrong answer this shape permits, and it is caught here rather
        # than silently becoming a time.
        raise VidError(
            f"The model named passages that are not in this video's index: {', '.join(unknown)}. "
            f"The index has {len(known)} passages, c0 to c{len(known) - 1}. "
            "Refusing rather than guessing what it meant."
        )
    return [_merge([known[i] for i in wanted], how="described", rationale=rationale)]


def find(chunks: list[Chunk], query: str, intelligence=None, seen: list[Chunk] | None = None) -> list[Hit]:
    """Literal first, then what was seen, then a model if one is available.

    SPEECH BEFORE VISION, on purpose. A transcript says what was actually meant;
    a frame description says what a model made of a picture. When both could
    answer, the transcript is the better evidence, and searching it first also
    costs nothing.
    """
    seen = seen or []
    if not chunks and not seen:
        raise VidError(
            "This video has nothing searchable in its index -- no speech, and no "
            "shot descriptions. Run `vid index <video>` for speech, or "
            "`vid index <video> --vision` to describe what is on screen."
        )

    hits = find_literal(chunks, query) if chunks else []
    if hits:
        return hits

    if seen:
        visual = find_literal(seen, query)
        if visual:
            # Relabelled so a caller can tell heard from seen. The distinction
            # matters: a description is a model's reading of a picture, and a
            # caller weighing a result deserves to know which it got.
            return [
                Hit(
                    start=hit.start,
                    end=hit.end,
                    chunk_ids=hit.chunk_ids,
                    text=hit.text,
                    how="seen",
                    rationale=hit.rationale,
                )
                for hit in visual
            ]

    if intelligence is None:
        raise VidError(
            f"Nothing in this video's index literally matches {query!r}, and matching it "
            "by meaning needs a model that is not configured. Either search for words the "
            "speaker actually used, or configure a provider -- `vid check` says how."
        )
    return find_described(chunks or seen, query, intelligence)
