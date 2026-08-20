from __future__ import annotations

from refgraph.domain.identity import IdentityIndex
from refgraph.domain.model import ExternalIds, Paper


def paper(
    id: str,
    title: str = "t",
    year: int | None = None,
    doi: str | None = None,
    arxiv: str | None = None,
    citation_count: int | None = None,
    venue: str | None = None,
    s2: str | None = None,
    openalex: str | None = None,
) -> Paper:
    return Paper(
        id=id,
        title=title,
        year=year,
        venue=venue,
        citation_count=citation_count,
        external_ids=ExternalIds(doi=doi, arxiv=arxiv, s2=s2, openalex=openalex),
    )


def test_merge_by_doi() -> None:
    index = IdentityIndex()
    a = index.register(
        paper("doi:10.1/x", title="Alpha", year=2020, doi="10.1/X", citation_count=5)
    )
    b = index.register(
        paper("s2:123", title="Alpha (draft)", year=2021, doi="10.1/x", citation_count=9, s2="123")
    )
    assert a.id == b.id == "doi:10.1/x"
    canonical = index.get("doi:10.1/x")
    assert canonical is not None
    assert canonical.title == "Alpha"  # first source wins
    assert canonical.year == 2020  # first source wins
    assert canonical.citation_count == 9  # max of the two
    assert canonical.external_ids.s2 == "123"  # gap filled


def test_merge_by_arxiv_when_no_doi() -> None:
    index = IdentityIndex()
    a = index.register(
        paper("arxiv:1706.03762", title="Attention Is All You Need", arxiv="1706.03762")
    )
    b = index.register(paper("openalex:W1", title="Attention Is All you Need", arxiv="1706.03762"))
    assert a.id == b.id == "arxiv:1706.03762"


def test_merge_by_title_year_when_no_ids() -> None:
    index = IdentityIndex()
    a = index.register(paper("s2:1", title="A  Study   Of Graphs", year=2019))
    b = index.register(paper("openalex:W9", title="a study of graphs", year=2019))
    assert a.id == b.id


def test_no_merge_different_years_same_title() -> None:
    index = IdentityIndex()
    a = index.register(paper("s2:1", title="Same Title", year=2019))
    b = index.register(paper("s2:2", title="Same Title", year=2020))
    assert a.id != b.id
