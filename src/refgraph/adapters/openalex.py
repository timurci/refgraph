from __future__ import annotations

import os
import re

from ..domain.model import ExternalIds, Paper
from ..domain.ports import FetchError
from ..infra.cache import ResponseCache
from ..infra.http import RateLimitedClient

_BASE = "https://api.openalex.org"
_SELECT = "id,doi,display_name,publication_year,cited_by_count,primary_location,authorships,ids"
_MAX_BATCH = 50
_CITES_PAGE = 200  # per_page cap; ranking is done server-side via sort


class OpenAlexSource:
    name = "openalex"

    def __init__(
        self,
        *,
        mailto: str | None = None,
        cache: ResponseCache | None = None,
        min_interval: float | None = None,
        backoff_429_base: float = 5.0,
    ) -> None:
        mailto = mailto or os.environ.get("OPENALEX_MAILTO")
        if min_interval is None:
            min_interval = 0.11  # polite pool: ~10 rps
        params_headers = {"User-Agent": f"refgraph (mailto:{mailto})"} if mailto else {}
        self._mailto = mailto
        self._http = RateLimitedClient(
            source=self.name,
            base_url=_BASE,
            min_interval=min_interval,
            headers=params_headers,
            cache=cache,
            backoff_429_base=backoff_429_base,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def search(self, query: str, limit: int = 10) -> list[Paper]:
        data = await self._http.get_json(
            "/works",
            {
                # Title search, not full-text: a title query should not match
                # papers that merely mention the phrase.
                "filter": f"display_name.search:{query}",
                "per_page": min(limit, 200),
                "select": _SELECT,
                **self._polite(),
            },
        )
        return [_to_paper(row) for row in data.get("results", [])]

    async def lookup(self, *, doi: str | None = None, arxiv: str | None = None) -> Paper | None:
        if not doi and arxiv:
            # arXiv assigns DataCite DOIs; OpenAlex indexes them.
            doi = f"10.48550/arXiv.{arxiv}"
        if not doi:
            return None
        try:
            data = await self._http.get_json(
                f"/works/doi:{doi}", {"select": _SELECT, **self._polite()}
            )
        except FetchError as exc:
            if exc.status_code == 404:
                return None
            raise
        return _to_paper(data)

    async def references(self, paper: Paper) -> list[Paper]:
        work_id = await self._resolve_work_id(paper)
        if work_id is None:
            return []
        data = await self._http.get_json(
            f"/works/{work_id}", {"select": "referenced_works", **self._polite()}
        )
        referenced = data.get("referenced_works") or []
        results: list[Paper] = []
        for chunk_start in range(0, len(referenced), _MAX_BATCH):
            chunk = referenced[chunk_start : chunk_start + _MAX_BATCH]
            batch = await self._http.get_json(
                "/works",
                {
                    "filter": "openalex_id:" + "|".join(chunk),
                    "per_page": _MAX_BATCH,
                    "select": _SELECT,
                    **self._polite(),
                },
            )
            results.extend(_to_paper(row) for row in batch.get("results", []))
        return results

    async def citations(self, paper: Paper) -> list[Paper]:
        work_id = await self._resolve_work_id(paper)
        if work_id is None:
            return []
        try:
            data = await self._http.get_json(
                "/works",
                {
                    "filter": f"cites:{work_id}",
                    "sort": "cited_by_count:desc",
                    "per_page": _CITES_PAGE,
                    "select": _SELECT,
                    **self._polite(),
                },
            )
        except FetchError as exc:
            if exc.status_code == 404:
                return []
            raise
        return [_to_paper(row) for row in data.get("results", [])]

    async def _resolve_work_id(self, paper: Paper) -> str | None:
        ids = paper.external_ids
        if ids.openalex:
            return ids.openalex
        if ids.doi:
            try:
                data = await self._http.get_json(
                    f"/works/doi:{ids.doi}", {"select": "id", **self._polite()}
                )
            except FetchError as exc:
                if exc.status_code == 404:
                    return None
                raise
            return _work_id(data)
        return None

    def _polite(self) -> dict[str, str]:
        return {"mailto": self._mailto} if self._mailto else {}


_ARXIV_URL_RE = re.compile(r"^https?://arxiv\.org/abs/([0-9]{4}\.[0-9]{4,5})(v\d+)?$", re.I)


def _work_id(data: dict) -> str | None:
    raw = data.get("id") or ""
    return raw.rsplit("/", 1)[-1] if raw else None


def _to_paper(work: dict) -> Paper:
    ids = work.get("ids") or {}
    openalex = _work_id(work)
    doi = work.get("doi") or ids.get("doi")
    doi = doi.replace("https://doi.org/", "") if doi else None
    arxiv = None
    location = work.get("primary_location") or {}
    landing = (location.get("landing_page_url") or "") if location else ""
    match = _ARXIV_URL_RE.match(landing)
    if match:
        arxiv = match.group(1)
    if doi:
        internal = f"doi:{doi.lower()}"
    elif arxiv:
        internal = f"arxiv:{arxiv.lower()}"
    elif openalex:
        internal = f"openalex:{openalex}"
    else:
        internal = f"title:{' '.join((work.get('display_name') or '').lower().split())}"
    venue = None
    if location and isinstance(location, dict):
        source = location.get("source") or {}
        venue = source.get("display_name")
    authors = [
        a.get("author", {}).get("display_name")
        for a in work.get("authorships") or []
        if a.get("author", {}).get("display_name")
    ]
    return Paper(
        id=internal,
        title=work.get("display_name") or "",
        year=work.get("publication_year"),
        venue=venue,
        citation_count=work.get("cited_by_count"),
        authors=authors,
        external_ids=ExternalIds(doi=doi, arxiv=arxiv, openalex=openalex, pmid=ids.get("pmid")),
    )
