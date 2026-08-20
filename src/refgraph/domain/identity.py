from __future__ import annotations

from .model import Paper


class IdentityIndex:
    """Maps external identities to internal node IDs and merges duplicates.

    Merge rule: DOI > arXiv > (normalized title, year). The first paper
    registered for an identity wins metadata conflicts; later papers only
    fill gaps (see Paper.merge_from).
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple, str] = {}
        self._papers: dict[str, Paper] = {}

    def register(self, paper: Paper) -> Paper:
        """Register a paper, merging with any existing match. Returns the
        canonical (possibly merged) paper. The canonical paper keeps the
        internal ID of whichever paper was registered first."""
        key = paper.match_key()
        if key is not None and key in self._by_key:
            canonical_id = self._by_key[key]
            canonical = self._papers[canonical_id]
            canonical.merge_from(paper)
            return canonical
        if paper.id in self._papers:
            canonical = self._papers[paper.id]
            canonical.merge_from(paper)
            return canonical
        if key is not None:
            self._by_key[key] = paper.id
        self._papers[paper.id] = paper
        return paper

    def get(self, internal_id: str) -> Paper | None:
        return self._papers.get(internal_id)

    def all_papers(self) -> list[Paper]:
        return list(self._papers.values())
