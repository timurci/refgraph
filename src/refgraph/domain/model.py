from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Direction(StrEnum):
    REF = "ref"
    CITE = "cite"


class ExternalIds(BaseModel):
    doi: str | None = None
    arxiv: str | None = None
    s2: str | None = None
    openalex: str | None = None
    pmid: str | None = None

    def is_empty(self) -> bool:
        return not any(getattr(self, f) for f in ("doi", "arxiv", "s2", "openalex", "pmid"))


class Paper(BaseModel):
    id: str
    title: str
    year: int | None = None
    venue: str | None = None
    citation_count: int | None = None
    authors: list[str] = Field(default_factory=list)
    external_ids: ExternalIds = Field(default_factory=ExternalIds)

    def merge_from(self, other: Paper) -> Paper:
        """First source (self) wins conflicts; other fills gaps. Returns self."""
        self.year = self.year if self.year is not None else other.year
        self.venue = self.venue if self.venue is not None else other.venue
        if other.citation_count is not None:
            if self.citation_count is None or other.citation_count > self.citation_count:
                self.citation_count = other.citation_count
        if not self.authors and other.authors:
            self.authors = list(other.authors)
        for field in ("doi", "arxiv", "s2", "openalex", "pmid"):
            own = getattr(self.external_ids, field)
            alt = getattr(other.external_ids, field)
            if own is None and alt is not None:
                setattr(self.external_ids, field, alt)
        return self

    def match_key(self) -> tuple | None:
        """Merge-priority key: DOI > arXiv > (normalized title, year)."""
        if self.external_ids.doi:
            return ("doi", self.external_ids.doi.lower())
        if self.external_ids.arxiv:
            return ("arxiv", self.external_ids.arxiv.lower())
        if self.title:
            return ("title", _normalize_title(self.title), self.year)
        return None


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())


class Provenance(BaseModel):
    directions: list[Direction]
    min_depth: int
    paths: list[list[str]]


class Node(BaseModel):
    paper: Paper
    provenance: Provenance


class Edge(BaseModel):
    source: str
    target: str
    relation: Literal["cites"] = "cites"


class GraphWarning(BaseModel):
    kind: str
    detail: str


class TraversalConfig(BaseModel):
    ref_depth: int = 1
    cite_depth: int = 1
    top_cites: int = 30
    year_min: int | None = None
    year_max: int | None = None
    sources: list[str] = Field(default_factory=lambda: ["semantic_scholar", "openalex"])


class GraphDocument(BaseModel):
    schema_version: int = 1
    seed: Paper
    config: TraversalConfig
    nodes: list[Node]
    edges: list[Edge]
    warnings: list[GraphWarning]

    def to_json(self, indent: int | None = 2) -> str:
        return self.model_dump_json(indent=indent)


class Candidate(BaseModel):
    """A resolution candidate for a raw user input."""

    paper: Paper
    match_confidence: float = 1.0
    source: list[str]
