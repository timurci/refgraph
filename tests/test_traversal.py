from __future__ import annotations

import asyncio

from refgraph.domain.model import Paper, TraversalConfig
from refgraph.domain.ports import FetchError
from refgraph.domain.traversal import build_graph

from .test_identity import paper


class FakeSource:
    """In-memory PaperSource keyed by paper id."""

    def __init__(
        self,
        name: str,
        refs: dict[str, list[Paper]] | None = None,
        cits: dict[str, list[Paper]] | None = None,
    ) -> None:
        self.name = name
        self.refs = refs or {}
        self.cits = cits or {}

    async def search(self, query: str, limit: int = 10) -> list[Paper]:
        return []

    async def lookup(self, *, doi: str | None = None, arxiv: str | None = None) -> Paper | None:
        return None

    async def references(self, paper: Paper) -> list[Paper]:
        return list(self.refs.get(paper.id, []))

    async def citations(self, paper: Paper) -> list[Paper]:
        return list(self.cits.get(paper.id, []))


class FailingSource:
    def __init__(self, name: str = "broken") -> None:
        self.name = name

    async def search(self, query: str, limit: int = 10) -> list[Paper]:
        raise FetchError(self.name, "boom")

    async def lookup(self, *, doi: str | None = None, arxiv: str | None = None) -> Paper | None:
        raise FetchError(self.name, "boom")

    async def references(self, paper: Paper) -> list[Paper]:
        raise FetchError(self.name, f"boom on references({paper.id})")

    async def citations(self, paper: Paper) -> list[Paper]:
        raise FetchError(self.name, f"boom on citations({paper.id})")


def _by_id(doc):
    return {n.paper.id: n for n in doc.nodes}


def test_pure_direction_two_levels() -> None:
    seed = paper("S", title="Seed", year=2020)
    a = paper("A", title="A", year=2018)
    b = paper("B", title="B", year=2015)
    x = paper("X", title="X", year=2001)
    c = paper("C", title="C", year=2021)
    d = paper("D", title="D", year=2022)
    z = paper("Z", title="Z", year=2019)  # only reachable by mixed traversal
    source = FakeSource(
        "fake",
        refs={"S": [a, b], "A": [b, x], "B": [], "C": [z]},
        cits={"S": [c], "C": [d]},
    )
    doc = asyncio.run(
        build_graph(
            seed,
            config=TraversalConfig(ref_depth=2, cite_depth=2, sources=["fake"]),
            sources=[source],
        )
    )
    nodes = _by_id(doc)

    assert set(nodes) == {"S", "A", "B", "X", "C", "D"}  # Z must be absent
    assert "Z" not in {p.id for p in (a, b, x, c, d)} or True

    # Seed node: depth 0, no directions
    assert nodes["S"].provenance.min_depth == 0
    assert nodes["S"].provenance.directions == []

    # A: ref, depth 1, path S->A
    assert nodes["A"].provenance.directions == ["ref"]
    assert nodes["A"].provenance.min_depth == 1
    assert nodes["A"].provenance.paths == [["S", "A"]]

    # B: discovered at depth 1 (shortest); the depth-2 path via A is not kept
    assert nodes["B"].provenance.min_depth == 1
    assert nodes["B"].provenance.paths == [["S", "B"]]

    # X: ref, depth 2, path S->A->X
    assert nodes["X"].provenance.min_depth == 2
    assert nodes["X"].provenance.paths == [["S", "A", "X"]]

    # C: cite, depth 1; D: cite, depth 2
    assert nodes["C"].provenance.directions == ["cite"]
    assert nodes["D"].provenance.min_depth == 2
    assert nodes["D"].provenance.paths == [["S", "C", "D"]]

    # Edges: A references B at depth 2 AND B is a depth-1 ref of S
    edge_set = {(e.source, e.target) for e in doc.edges}
    assert edge_set == {("S", "A"), ("S", "B"), ("A", "B"), ("A", "X"), ("C", "S"), ("D", "C")}
    assert doc.warnings == []


def test_cite_capping_dedupes_across_sources_then_caps() -> None:
    seed = paper("S", title="Seed")
    p1 = paper("doi:10.1/p1", title="P1", doi="10.1/p1", citation_count=100)
    p1_dup = paper("openalex:W1", title="P1 (dup)", doi="10.1/p1", citation_count=100)
    p2 = paper("P2", title="P2", citation_count=1)
    p3 = paper("P3", title="P3", citation_count=50)
    s1 = FakeSource("s1", cits={"S": [p1, p2]})
    s2 = FakeSource("s2", cits={"S": [p1_dup, p3]})
    doc = asyncio.run(
        build_graph(
            seed,
            config=TraversalConfig(top_cites=2, sources=["s1", "s2"]),
            sources=[s1, s2],
        )
    )
    nodes = _by_id(doc)
    # p1 merged across sources (canonical id from s1, first source wins);
    # ranking: p1 (100) > p3 (50) > p2 (1) -> top 2 keeps p1, p3
    assert set(nodes) == {"S", "doi:10.1/p1", "P3"}
    assert nodes["doi:10.1/p1"].paper.citation_count == 100
    assert nodes["doi:10.1/p1"].provenance.directions == ["cite"]
    assert doc.warnings and doc.warnings[0].kind == "citations_truncated"
    assert "kept top 2 of 3" in doc.warnings[0].detail


def test_fetch_failure_records_warning_and_continues() -> None:
    seed = paper("S", title="Seed")
    a = paper("A", title="A")
    good = FakeSource("good", refs={"S": [a]})
    bad = FailingSource("bad")
    doc = asyncio.run(
        build_graph(
            seed,
            config=TraversalConfig(ref_depth=1, cite_depth=1, sources=["good", "bad"]),
            sources=[good, bad],
        )
    )
    nodes = _by_id(doc)
    assert "A" in nodes  # data from the good source survives
    kinds = {w.kind for w in doc.warnings}
    assert kinds == {"fetch_failed"}
    assert all("bad:" in w.detail for w in doc.warnings)


def test_all_sources_fail_leaves_seed_only() -> None:
    seed = paper("S", title="Seed")
    doc = asyncio.run(
        build_graph(
            seed,
            config=TraversalConfig(sources=["broken"]),
            sources=[FailingSource()],
        )
    )
    assert [n.paper.id for n in doc.nodes] == ["S"]
    assert doc.edges == []
    assert len(doc.warnings) == 2  # references + citations of the seed


def test_self_citation_edge_no_duplicate_seed() -> None:
    seed = paper("S", title="Seed")
    c = paper("C", title="C")
    source = FakeSource("fake", refs={"C": [seed]}, cits={"S": [c]})
    doc = asyncio.run(build_graph(seed, config=TraversalConfig(sources=["fake"]), sources=[source]))
    assert len([n for n in doc.nodes if n.paper.id == "S"]) == 1
    edge_set = {(e.source, e.target) for e in doc.edges}
    assert ("C", "S") in edge_set  # C cites the seed (discovered via C's refs)


def test_node_discovered_from_both_directions() -> None:
    seed = paper("S", title="Seed")
    r = paper("R", title="R", year=2010)  # seed's reference
    source = FakeSource("fake", refs={"S": [r]}, cits={"S": [r]})
    # r both is cited by seed and cites the seed (self-referential oddity, but
    # exercises dual-direction provenance)
    doc = asyncio.run(build_graph(seed, config=TraversalConfig(sources=["fake"]), sources=[source]))
    prov = _by_id(doc)["R"].provenance
    assert set(prov.directions) == {"ref", "cite"}
    assert prov.min_depth == 1
    assert sorted(prov.paths) == [["S", "R"]]


def test_determinism_identical_documents() -> None:
    seed = paper("S", title="Seed")
    a = paper("A", title="A", year=2018)
    b = paper("B", title="B", year=2015)
    c = paper("C", title="C", year=2021, citation_count=10)
    d = paper("D", title="D", year=2022, citation_count=3)
    e = paper("E", title="E", year=2023, citation_count=5)

    def make() -> FakeSource:
        return FakeSource(
            "fake",
            refs={"S": [a, b], "A": [b]},
            cits={"S": [c, d, e], "C": [d]},
        )

    config = TraversalConfig(ref_depth=2, cite_depth=2, top_cites=2, sources=["fake"])
    doc1 = asyncio.run(build_graph(seed, config=config, sources=[make()]))
    doc2 = asyncio.run(build_graph(seed, config=config, sources=[make()]))
    assert doc1.to_json() == doc2.to_json()


def test_zero_depths_seed_only() -> None:
    seed = paper("S", title="Seed")
    source = FakeSource("fake", refs={"S": [paper("A", title="A")]})
    doc = asyncio.run(
        build_graph(
            seed,
            config=TraversalConfig(ref_depth=0, cite_depth=0, sources=["fake"]),
            sources=[source],
        )
    )
    assert [n.paper.id for n in doc.nodes] == ["S"]
    assert doc.edges == []
