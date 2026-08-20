from __future__ import annotations

from .model import Paper, TraversalConfig


class CitationCappingPolicy:
    """Filter -> rank -> top-N for incoming citations.

    Deterministic: citers are filtered by optional year range, ranked by
    citation count descending (missing counts sort last), tiebroken by
    paper ID ascending, then capped at ``top_cites``.
    """

    def __init__(self, config: TraversalConfig) -> None:
        self.config = config

    def apply(self, citers: list[Paper]) -> list[Paper]:
        cfg = self.config
        filtered = [
            p
            for p in citers
            if (cfg.year_min is None or (p.year or 0) >= cfg.year_min)
            and (cfg.year_max is None or (p.year or 10**9) <= cfg.year_max)
        ]
        ranked = sorted(
            filtered,
            key=lambda p: (-(p.citation_count or -1), p.id),
        )
        return ranked[: cfg.top_cites]
