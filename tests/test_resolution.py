from __future__ import annotations

import asyncio

from refgraph.domain.resolution import parse_input, resolve

from .test_identity import paper
from .test_traversal import FakeSource


def test_parse_input_doi() -> None:
    assert parse_input("10.18653/v1/N18-3011") == (
        "10.18653/v1/N18-3011",
        None,
        "10.18653/v1/N18-3011",
    )


def test_parse_input_arxiv_variants() -> None:
    assert parse_input("1706.03762")[1] == "1706.03762"
    assert parse_input("arXiv:1706.03762v5")[1] == "1706.03762"
    doi, arxiv, _ = parse_input("Attention is all you need")
    assert (doi, arxiv) == (None, None)


def test_resolve_exact_doi_prefers_lookup() -> None:
    exact = paper("doi:10.1234/x", title="The Paper", doi="10.1234/x")
    other = paper("doi:10.1234/y", title="Something Else", doi="10.1234/y")
    s = FakeSource("fake", refs={}, cits={})
    s.lookup = lambda **kw: asyncio.sleep(0, result=exact)  # type: ignore[assignment]
    s.search = lambda query, limit=10: asyncio.sleep(0, result=[other])  # type: ignore[assignment]
    candidates = asyncio.run(resolve("10.1234/x", sources=[s]))
    assert len(candidates) == 1
    assert candidates[0].paper.id == "doi:10.1234/x"
    assert candidates[0].match_confidence == 1.0


def test_resolve_title_ranks_by_similarity_and_merges_sources() -> None:
    good = paper("s2:1", title="Attention Is All You Need", citation_count=100000)
    same_openalex = paper("openalex:W1", title="Attention is All you Need", citation_count=100000)
    off_topic = paper("s2:2", title="ImageNet Classification with CNNs", citation_count=5)
    s1 = FakeSource("s1")
    s1.search = lambda query, limit=10: asyncio.sleep(0, result=[off_topic])  # type: ignore[assignment]
    s2 = FakeSource("s2")
    s2.search = lambda query, limit=10: asyncio.sleep(0, result=[good, same_openalex])  # type: ignore[assignment]
    candidates = asyncio.run(resolve("attention is all you need", sources=[s1, s2]))
    assert len(candidates) == 2
    top = candidates[0]
    assert top.paper.id == "s2:1"  # first-registered canonical (s2 order)
    assert top.source == ["s2"]  # openalex dup merged into canonical... but listed?
    assert top.match_confidence > 0.9
    assert candidates[1].paper.id == "s2:2"


def test_resolve_empty_when_nothing_found() -> None:
    candidates = asyncio.run(resolve("zzz no match", sources=[FakeSource("s")]))
    assert candidates == []
