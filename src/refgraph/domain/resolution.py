from __future__ import annotations

import asyncio
import difflib
import re

from .identity import IdentityIndex
from .model import Candidate, Paper
from .ports import FetchError, PaperSource

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_ARXIV_RE = re.compile(r"^(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$", re.IGNORECASE)


def parse_input(raw: str) -> tuple[str | None, str | None, str]:
    """Classify raw input -> (doi, arxiv, title-ish query)."""
    text = raw.strip()
    if _DOI_RE.match(text):
        return text, None, text
    match = _ARXIV_RE.match(text)
    if match:
        return None, match.group(1), text
    return None, None, text


async def resolve(raw: str, *, sources: list[PaperSource], limit: int = 10) -> list[Candidate]:
    """Resolve raw user input to ranked candidate papers.

    DOI / arXiv ID inputs are looked up exactly (confidence 1.0); free text
    is searched on every source and scored by title similarity. Candidates
    from multiple sources are merged into one (with all source names listed).
    """
    doi, arxiv, query = parse_input(raw)

    if doi or arxiv:
        results = await asyncio.gather(*(_safe_lookup(s, doi=doi, arxiv=arxiv) for s in sources))
        index = IdentityIndex()
        found: dict[str, Candidate] = {}
        for src, paper in zip(sources, results, strict=True):
            if paper is None:
                continue
            canonical = index.register(paper)
            if canonical.id in found:
                found[canonical.id].source.append(src.name)
            else:
                found[canonical.id] = Candidate(
                    paper=canonical, match_confidence=1.0, source=[src.name]
                )
        if found:
            return sorted(found.values(), key=lambda c: c.paper.id)
        # Identifier lookup missed everywhere: do NOT fall back to title
        # search with the raw identifier string (it would match random
        # papers). An unresolved identifier is simply unresolved.
        return []

    per_source = await asyncio.gather(*(_safe_search(s, query, limit) for s in sources))
    index = IdentityIndex()
    found: dict[str, Candidate] = {}
    for src, papers in zip(sources, per_source, strict=True):
        for paper in papers:
            canonical = index.register(paper)
            confidence = _title_similarity(query, canonical.title)
            if canonical.id in found:
                if src.name not in found[canonical.id].source:
                    found[canonical.id].source.append(src.name)
                found[canonical.id].match_confidence = max(
                    found[canonical.id].match_confidence, confidence
                )
            else:
                found[canonical.id] = Candidate(
                    paper=canonical, match_confidence=confidence, source=[src.name]
                )
    return sorted(
        found.values(),
        key=lambda c: (
            -c.match_confidence,
            -(c.paper.citation_count or -1),
            c.paper.id,
        ),
    )


def _title_similarity(query: str, title: str) -> float:
    a = " ".join(query.lower().split())
    b = " ".join((title or "").lower().split())
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


async def _safe_lookup(src: PaperSource, *, doi: str | None, arxiv: str | None) -> Paper | None:
    try:
        return await src.lookup(doi=doi, arxiv=arxiv)
    except FetchError:
        return None  # best-effort: other sources may still resolve it


async def _safe_search(src: PaperSource, query: str, limit: int) -> list[Paper]:
    try:
        return await src.search(query, limit=limit)
    except FetchError:
        return []
