from __future__ import annotations

from ..domain.ports import PaperSource
from ..infra.cache import ResponseCache
from .openalex import OpenAlexSource
from .semantic_scholar import SemanticScholarSource

__all__ = ["OpenAlexSource", "SemanticScholarSource", "default_sources", "source_names"]

_NAMED: dict[str, type] = {
    SemanticScholarSource.name: SemanticScholarSource,
    OpenAlexSource.name: OpenAlexSource,
}


def source_names() -> list[str]:
    return list(_NAMED)


def default_sources(
    *,
    names: list[str] | None = None,
    cache: ResponseCache | None = None,
    s2_api_key: str | None = None,
    openalex_mailto: str | None = None,
    s2_min_interval: float | None = None,
    openalex_min_interval: float | None = None,
) -> list[PaperSource]:
    """Construct the configured sources. Fixed order (S2 first) keeps
    metadata-merge results deterministic."""
    if not names:
        names = [SemanticScholarSource.name, OpenAlexSource.name]
    unknown = [n for n in names if n not in _NAMED]
    if unknown:
        raise ValueError(f"unknown source(s) {unknown}; available: {source_names()}")
    ordered = [n for n in (SemanticScholarSource.name, OpenAlexSource.name) if n in names]
    sources: list[PaperSource] = []
    for name in ordered:
        if name == SemanticScholarSource.name:
            sources.append(
                SemanticScholarSource(api_key=s2_api_key, cache=cache, min_interval=s2_min_interval)
            )
        else:
            sources.append(
                OpenAlexSource(
                    mailto=openalex_mailto,
                    cache=cache,
                    min_interval=openalex_min_interval,
                )
            )
    return sources
