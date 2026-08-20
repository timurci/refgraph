from __future__ import annotations

import os

from ..domain.model import ExternalIds, Paper
from ..domain.ports import FetchError
from ..infra.cache import ResponseCache
from ..infra.http import RateLimitedClient

_BASE = "https://api.semanticscholar.org/graph/v1"
_FIELDS = "title,year,venue,citationCount,externalIds,authors"
_PAGE_LIMIT = 1000
_MAX_REF_PAGES = 10  # safety bound: papers with >10k references are truncated


class SemanticScholarSource:
    name = "semantic_scholar"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        cache: ResponseCache | None = None,
        min_interval: float | None = None,
        backoff_429_base: float = 5.0,
    ) -> None:
        api_key = api_key or os.environ.get("S2_API_KEY")
        headers = {"x-api-key": api_key} if api_key else {}
        if min_interval is None:
            min_interval = 1.1 if api_key else 3.05  # keyed: 1 rps; unkeyed pool is stricter
        self._http = RateLimitedClient(
            source=self.name,
            base_url=_BASE,
            min_interval=min_interval,
            headers=headers,
            cache=cache,
            backoff_429_base=backoff_429_base,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def search(self, query: str, limit: int = 10) -> list[Paper]:
        try:
            data = await self._http.get_json(
                "/paper/search",
                {"query": query, "fields": _FIELDS, "limit": min(limit, 100)},
            )
        except FetchError as exc:
            if exc.status_code == 404:  # no matches
                return []
            raise
        return [_to_paper(row) for row in data.get("data", [])]

    async def lookup(self, *, doi: str | None = None, arxiv: str | None = None) -> Paper | None:
        paper_id = self._s2_id(doi=doi, arxiv=arxiv)
        if paper_id is None:
            return None
        try:
            data = await self._http.get_json(f"/paper/{paper_id}", {"fields": _FIELDS})
        except FetchError as exc:
            if exc.status_code == 404:
                return None
            raise
        return _to_paper(data)

    async def references(self, paper: Paper) -> list[Paper]:
        paper_id = self._s2_id_of(paper)
        if paper_id is None:
            return []
        offset = 0
        results: list[Paper] = []
        for _ in range(_MAX_REF_PAGES):
            data = await self._http.get_json(
                f"/paper/{paper_id}/references",
                {"fields": _FIELDS, "limit": _PAGE_LIMIT, "offset": offset},
            )
            results.extend(_to_paper(row["citedPaper"]) for row in data.get("data", []))
            next_offset = data.get("next")
            if next_offset is None:
                return results
            offset = next_offset
        return results

    async def citations(self, paper: Paper) -> list[Paper]:
        paper_id = self._s2_id_of(paper)
        if paper_id is None:
            return []
        try:
            data = await self._http.get_json(
                f"/paper/{paper_id}/citations",
                {"fields": _FIELDS, "limit": _PAGE_LIMIT},
            )
        except FetchError as exc:
            if exc.status_code == 404:
                return []
            raise
        return [_to_paper(row["citingPaper"]) for row in data.get("data", [])]

    @staticmethod
    def _s2_id(*, doi: str | None, arxiv: str | None) -> str | None:
        if doi:
            return f"DOI:{doi}"
        if arxiv:
            return f"ARXIV:{arxiv}"
        return None

    @classmethod
    def _s2_id_of(cls, paper: Paper) -> str | None:
        ids = paper.external_ids
        if ids.s2:
            return ids.s2
        return cls._s2_id(doi=ids.doi, arxiv=ids.arxiv)


def _to_paper(data: dict) -> Paper:
    ext = data.get("externalIds") or {}
    doi = ext.get("DOI")
    arxiv = ext.get("ArXiv")
    s2 = data.get("paperId")
    pmid = ext.get("PubMed")
    if doi:
        internal = f"doi:{doi.lower()}"
    elif arxiv:
        internal = f"arxiv:{arxiv.lower()}"
    elif s2:
        internal = f"s2:{s2}"
    else:
        internal = f"title:{' '.join((data.get('title') or '').lower().split())}"
    authors = [a.get("name") for a in data.get("authors") or [] if a.get("name")]
    return Paper(
        id=internal,
        title=data.get("title") or "",
        year=data.get("year"),
        venue=data.get("venue") or None,
        citation_count=data.get("citationCount"),
        authors=authors,
        external_ids=ExternalIds(doi=doi, arxiv=arxiv, s2=s2, pmid=pmid),
    )
