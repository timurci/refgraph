from __future__ import annotations

import pytest
import respx
from httpx import Response

from refgraph.adapters import OpenAlexSource, SemanticScholarSource
from refgraph.infra.cache import ResponseCache

S2 = "https://api.semanticscholar.org/graph/v1"
OA = "https://api.openalex.org"

S2_PAPER = {
    "paperId": "abc123",
    "title": "Attention Is All You Need",
    "year": 2017,
    "venue": "NeurIPS",
    "citationCount": 100000,
    "authors": [{"name": "Ashish Vaswani"}],
    "externalIds": {"DOI": "10.5555/3295222", "ArXiv": "1706.03762", "PubMed": None},
}


def s2_ref(pid: str, title: str) -> dict:
    return {"citedPaper": {"paperId": pid, "title": title, "externalIds": {}}}


def s2_cite(pid: str, title: str) -> dict:
    return {"citingPaper": {"paperId": pid, "title": title, "externalIds": {}}}


@pytest.fixture()
def s2() -> SemanticScholarSource:
    return SemanticScholarSource(cache=None, min_interval=0.0)


@respx.mock
async def test_s2_lookup_by_doi(s2: SemanticScholarSource) -> None:
    route = respx.get(f"{S2}/paper/DOI:10.5555/3295222").mock(
        return_value=Response(200, json=S2_PAPER)
    )
    paper = await s2.lookup(doi="10.5555/3295222")
    assert paper is not None
    assert paper.id == "doi:10.5555/3295222"
    assert paper.title == "Attention Is All You Need"
    assert paper.external_ids.arxiv == "1706.03762"
    assert paper.authors == ["Ashish Vaswani"]
    assert route.called


@respx.mock
async def test_s2_lookup_404_returns_none(s2: SemanticScholarSource) -> None:
    respx.get(f"{S2}/paper/DOI:10.1/missing").mock(return_value=Response(404, json={}))
    assert await s2.lookup(doi="10.1/missing") is None


@respx.mock
async def test_s2_search_404_returns_empty(s2: SemanticScholarSource) -> None:
    respx.get(f"{S2}/paper/search").mock(return_value=Response(404, json={}))
    assert await s2.search("zzz nope") == []


@respx.mock
async def test_s2_references_pagination(s2: SemanticScholarSource) -> None:
    respx.get(f"{S2}/paper/abc123/references").mock(
        side_effect=[
            Response(200, json={"offset": 0, "next": 2, "data": [s2_ref("r1", "Ref One")]}),
            Response(200, json={"offset": 2, "next": None, "data": [s2_ref("r2", "Ref Two")]}),
        ]
    )
    seed = S2_PAPER and _s2_paper()
    refs = await s2.references(seed)
    assert [r.title for r in refs] == ["Ref One", "Ref Two"]


@respx.mock
async def test_s2_citations(s2: SemanticScholarSource) -> None:
    respx.get(f"{S2}/paper/abc123/citations").mock(
        return_value=Response(200, json={"data": [s2_cite("c1", "Citer One")]})
    )
    cits = await s2.citations(_s2_paper())
    assert [c.title for c in cits] == ["Citer One"]


def _s2_paper():
    from refgraph.domain.model import ExternalIds, Paper

    return Paper(
        id="doi:10.5555/3295222",
        title="Attention Is All You Need",
        year=2017,
        external_ids=ExternalIds(doi="10.5555/3295222", arxiv="1706.03762", s2="abc123"),
    )


OA_WORK = {
    "id": "https://openalex.org/W2775230599",
    "doi": "https://doi.org/10.5555/3295222",
    "display_name": "Attention Is All You Need",
    "publication_year": 2017,
    "cited_by_count": 100000,
    "primary_location": {
        "landing_page_url": "https://arxiv.org/abs/1706.03762",
        "source": {"display_name": "NeurIPS"},
    },
    "authorships": [{"author": {"display_name": "Ashish Vaswani"}}],
    "ids": {
        "openalex": "https://openalex.org/W2775230599",
        "doi": "https://doi.org/10.5555/3295222",
    },
}


@pytest.fixture()
def oa() -> OpenAlexSource:
    return OpenAlexSource(cache=None, min_interval=0.0)


@respx.mock
async def test_oa_lookup_by_doi(oa: OpenAlexSource) -> None:
    respx.get(f"{OA}/works/doi:10.5555/3295222").mock(return_value=Response(200, json=OA_WORK))
    paper = await oa.lookup(doi="10.5555/3295222")
    assert paper is not None
    assert paper.id == "doi:10.5555/3295222"  # DOI wins over openalex id
    assert paper.external_ids.openalex == "W2775230599"
    assert paper.external_ids.arxiv == "1706.03762"  # extracted from landing page
    assert paper.venue == "NeurIPS"


@respx.mock
async def test_oa_lookup_arxiv_via_datadoi(oa: OpenAlexSource) -> None:
    route = respx.get(f"{OA}/works/doi:10.48550/arXiv.1706.03762").mock(
        return_value=Response(200, json=OA_WORK)
    )
    paper = await oa.lookup(arxiv="1706.03762")
    assert paper is not None
    assert route.called


@respx.mock
async def test_oa_references_batches(oa: OpenAlexSource) -> None:
    respx.get(f"{OA}/works/W2775230599").mock(
        return_value=Response(
            200, json={"referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"]}
        )
    )
    respx.get(f"{OA}/works").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "display_name": "Ref One",
                        "publication_year": 2016,
                        "cited_by_count": 3,
                        "ids": {"openalex": "https://openalex.org/W1"},
                    },
                    {
                        "id": "https://openalex.org/W2",
                        "display_name": "Ref Two",
                        "publication_year": 2015,
                        "cited_by_count": 7,
                        "ids": {"openalex": "https://openalex.org/W2"},
                    },
                ]
            },
        )
    )
    refs = await oa.references(_oa_paper())
    assert [r.title for r in refs] == ["Ref One", "Ref Two"]
    assert refs[0].external_ids.openalex == "W1"


@respx.mock
async def test_oa_citations_sorted_by_citations(oa: OpenAlexSource) -> None:
    route = respx.get(f"{OA}/works").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W9",
                        "display_name": "Citer",
                        "cited_by_count": 42,
                        "ids": {"openalex": "https://openalex.org/W9"},
                    }
                ]
            },
        )
    )
    cits = await oa.citations(_oa_paper())
    assert [c.title for c in cits] == ["Citer"]
    request = route.calls.last.request
    assert "filter=cites%3AW2775230599" in str(request.url)
    assert "sort=cited_by_count%3Adesc" in str(request.url)


def _oa_paper():
    from refgraph.domain.model import ExternalIds, Paper

    return Paper(
        id="doi:10.5555/3295222",
        title="Attention Is All You Need",
        external_ids=ExternalIds(doi="10.5555/3295222", openalex="W2775230599"),
    )


@respx.mock
async def test_cache_hits_http_once(tmp_path) -> None:
    cache = ResponseCache(str(tmp_path / "cache.sqlite3"))
    try:
        s2 = SemanticScholarSource(cache=cache, min_interval=0.0)
        respx.get(f"{S2}/paper/DOI:10.5555/3295222").mock(return_value=Response(200, json=S2_PAPER))
        await s2.lookup(doi="10.5555/3295222")
        await s2.lookup(doi="10.5555/3295222")
        await s2.aclose()
        # Fresh source, same cache: still no second HTTP call.
        s2_again = SemanticScholarSource(cache=cache, min_interval=0.0)
        paper = await s2_again.lookup(doi="10.5555/3295222")
        await s2_again.aclose()
        assert paper is not None
        assert respx.calls.call_count == 1
    finally:
        cache.close()


@respx.mock
async def test_retries_on_429_then_succeeds(tmp_path) -> None:
    s2 = SemanticScholarSource(cache=None, min_interval=0.0, backoff_429_base=0.0)
    respx.get(f"{S2}/paper/DOI:10.5555/3295222").mock(
        side_effect=[
            Response(429, headers={"retry-after": "0"}),
            Response(200, json=S2_PAPER),
        ]
    )
    paper = await s2.lookup(doi="10.5555/3295222")
    await s2.aclose()
    assert paper is not None
    assert respx.calls.call_count == 2
