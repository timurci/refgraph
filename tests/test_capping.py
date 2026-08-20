from __future__ import annotations

from refgraph.domain.capping import CitationCappingPolicy
from refgraph.domain.model import TraversalConfig

from .test_identity import paper


def test_rank_and_cap() -> None:
    policy = CitationCappingPolicy(TraversalConfig(top_cites=3))
    citers = [
        paper("a", citation_count=10),
        paper("b", citation_count=50),
        paper("c", citation_count=50),
        paper("d", citation_count=99),
        paper("e", citation_count=None),
    ]
    kept = policy.apply(citers)
    assert [p.id for p in kept] == ["d", "b", "c"]


def test_deterministic_tiebreak_by_id() -> None:
    policy = CitationCappingPolicy(TraversalConfig(top_cites=10))
    citers = [
        paper("z", citation_count=7),
        paper("a", citation_count=7),
        paper("m", citation_count=7),
    ]
    assert [p.id for p in policy.apply(citers)] == ["a", "m", "z"]


def test_year_filter_before_rank() -> None:
    policy = CitationCappingPolicy(TraversalConfig(top_cites=2, year_min=2020, year_max=2023))
    citers = [
        paper("old", citation_count=100, year=2019),
        paper("recent", citation_count=5, year=2021),
        paper("future", citation_count=50, year=2024),
        paper("edge-min", citation_count=1, year=2020),
    ]
    assert [p.id for p in policy.apply(citers)] == ["recent", "edge-min"]


def test_unknown_year_fails_year_filter() -> None:
    # Unknown-year citers are dropped conservatively when a year filter is set.
    policy = CitationCappingPolicy(TraversalConfig(top_cites=5, year_min=2000))
    assert policy.apply([paper("no-year", citation_count=1)]) == []
    policy_max = CitationCappingPolicy(TraversalConfig(top_cites=5, year_max=2000))
    assert policy_max.apply([paper("no-year", citation_count=1)]) == []


def test_no_filter_keeps_unknown_year() -> None:
    policy = CitationCappingPolicy(TraversalConfig(top_cites=5))
    kept = policy.apply([paper("no-year", citation_count=1)])
    assert [p.id for p in kept] == ["no-year"]
