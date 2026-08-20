from __future__ import annotations

from typing import Protocol

from .model import Paper


class FetchError(Exception):
    """Raised when a source fails to answer a request after retries."""

    def __init__(self, source: str, detail: str, status_code: int | None = None) -> None:
        super().__init__(detail)
        self.source = source
        self.detail = detail
        self.status_code = status_code

    def __str__(self) -> str:
        return self.detail


class PaperSource(Protocol):
    """Port: a scholarly data source the domain can query."""

    name: str

    async def search(self, query: str, limit: int = 10) -> list[Paper]: ...

    async def lookup(self, *, doi: str | None = None, arxiv: str | None = None) -> Paper | None: ...

    async def references(self, paper: Paper) -> list[Paper]: ...

    async def citations(self, paper: Paper) -> list[Paper]: ...
