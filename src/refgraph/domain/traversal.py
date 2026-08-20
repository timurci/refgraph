from __future__ import annotations

import asyncio

from .capping import CitationCappingPolicy
from .identity import IdentityIndex
from .model import (
    Direction,
    Edge,
    GraphDocument,
    GraphWarning,
    Node,
    Paper,
    Provenance,
    TraversalConfig,
)
from .ports import FetchError, PaperSource

_RELATION = {
    Direction.REF: "references",
    Direction.CITE: "citations",
}


async def build_graph(
    seed: Paper,
    *,
    config: TraversalConfig,
    sources: list[PaperSource],
) -> GraphDocument:
    """Build a pure-direction citation graph around ``seed``.

    Ref traversal follows references only (backward in time); cite traversal
    follows citations only (forward in time). The two depths are independent.
    Deterministic: same seed, config, and source data produce the same
    document regardless of network timing.
    """
    index = IdentityIndex()
    seed = index.register(seed)
    policy = CitationCappingPolicy(config)
    warnings: list[GraphWarning] = []
    # node id -> direction -> (min depth, discoverer paths at that depth)
    prov: dict[str, dict[Direction, tuple[int, list[list[str]]]]] = {}
    edges: set[tuple[str, str]] = set()

    async def fetch_neighbors(paper: Paper, direction: Direction) -> dict[str, list[Paper] | None]:
        relation = _RELATION[direction]

        async def one(src: PaperSource) -> tuple[str, list[Paper] | None]:
            try:
                return src.name, await getattr(src, relation)(paper)
            except FetchError as exc:
                warnings.append(
                    GraphWarning(
                        kind="fetch_failed",
                        detail=f"{exc.source}: {relation} of {paper.id}: {exc}",
                    )
                )
                return src.name, None

        pairs = await asyncio.gather(*(one(s) for s in sources))
        return dict(pairs)

    def parent_paths(pid: str, direction: Direction) -> list[list[str]]:
        if pid == seed.id:
            return [[seed.id]]
        entry = prov.get(pid, {}).get(direction)
        return list(entry[1]) if entry else []

    def record(nid: str, direction: Direction, depth: int, new_paths: list[list[str]]) -> bool:
        """Record provenance; returns True if this is a first discovery in
        this direction (i.e. the node joins the next frontier)."""
        by_dir = prov.setdefault(nid, {})
        if direction not in by_dir:
            by_dir[direction] = (depth, new_paths)
            return True
        min_depth, paths = by_dir[direction]
        if depth < min_depth:
            by_dir[direction] = (depth, new_paths)
            return False  # already expanded at a shallower depth
        if depth == min_depth:
            existing = {tuple(p) for p in paths}
            for p in new_paths:
                if tuple(p) not in existing:
                    paths.append(p)
        return False

    async def expand(direction: Direction, max_depth: int) -> None:
        frontier = [seed.id]
        depth = 0
        while frontier and depth < max_depth:
            depth += 1
            papers = sorted(
                (index.get(pid) for pid in frontier),  # type: ignore[arg-type]
                key=lambda p: p.id,
            )
            results = await asyncio.gather(*(fetch_neighbors(p, direction) for p in papers))
            next_frontier: list[str] = []
            for paper, by_source in zip(papers, results, strict=True):
                neighbors: list[Paper] = []
                for src in sources:  # fixed source order => deterministic merge
                    listing = by_source.get(src.name)
                    if listing:
                        neighbors.extend(listing)
                if not neighbors:
                    continue
                # Deduplicate across sources via identity before capping.
                canonical: list[Paper] = []
                seen: set[str] = set()
                for neighbor in neighbors:
                    merged = index.register(neighbor)
                    if merged.id not in seen:
                        seen.add(merged.id)
                        canonical.append(merged)
                if direction is Direction.CITE:
                    if len(canonical) > config.top_cites:
                        warnings.append(
                            GraphWarning(
                                kind="citations_truncated",
                                detail=(
                                    f"{paper.id}: kept top {config.top_cites} "
                                    f"of {len(canonical)} citing papers"
                                ),
                            )
                        )
                    kept = policy.apply(canonical)
                else:
                    kept = canonical
                for neighbor in kept:
                    if direction is Direction.REF:
                        edges.add((paper.id, neighbor.id))
                    else:
                        edges.add((neighbor.id, paper.id))
                    if neighbor.id == seed.id:
                        continue
                    paths = [p + [neighbor.id] for p in parent_paths(paper.id, direction)]
                    if record(neighbor.id, direction, depth, paths):
                        next_frontier.append(neighbor.id)
            frontier = sorted(next_frontier)

    await expand(Direction.REF, config.ref_depth)
    await expand(Direction.CITE, config.cite_depth)

    nodes: list[Node] = [
        Node(paper=index.get(nid), provenance=_provenance_of(nid, prov))  # type: ignore[arg-type]
        for nid in sorted(prov)
    ]
    nodes.append(Node(paper=seed, provenance=Provenance(directions=[], min_depth=0, paths=[])))
    nodes.sort(key=lambda n: n.paper.id)
    edge_list = [Edge(source=s, target=t) for s, t in sorted(edges)]
    return GraphDocument(
        seed=seed,
        config=config,
        nodes=nodes,
        edges=edge_list,
        warnings=warnings,
    )


def _provenance_of(
    nid: str, prov: dict[str, dict[Direction, tuple[int, list[list[str]]]]]
) -> Provenance:
    by_dir = prov.get(nid, {})
    directions = [d for d in (Direction.REF, Direction.CITE) if d in by_dir]
    min_depth = min((by_dir[d][0] for d in directions), default=0)
    paths: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for d in directions:
        for p in by_dir[d][1]:
            if tuple(p) not in seen:
                seen.add(tuple(p))
                paths.append(p)
    paths.sort()
    return Provenance(directions=directions, min_depth=min_depth, paths=paths)
