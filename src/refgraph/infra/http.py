from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from ..domain.ports import FetchError
from .cache import ResponseCache

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RateLimitedClient:
    """Async JSON GET client with per-client rate limiting, retries with
    backoff, and an optional persistent response cache.

    Determinism note: only successful (200) responses are cached, so cached
    replays yield the same data the original run saw.
    """

    def __init__(
        self,
        *,
        source: str,
        base_url: str,
        min_interval: float,
        headers: dict[str, str] | None = None,
        cache: ResponseCache | None = None,
        retries: int = 3,
        timeout: float = 30.0,
        backoff_429_base: float = 5.0,
    ) -> None:
        self.source = source
        self.base_url = base_url.rstrip("/")
        self.min_interval = min_interval
        self.retries = retries
        self._backoff_429_base = backoff_429_base
        self._cache = cache
        self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout)
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        query = urlencode(params or {})
        cache_key = f"{self.source}:{path}?{query}"
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        url = f"{path}?{query}" if query else path
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            await self._throttle()
            try:
                response = await self._client.get(url, headers=extra_headers)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == self.retries:
                    break
                await asyncio.sleep(self._backoff(attempt))
                continue
            if response.status_code == 200:
                text = response.text
                if self._cache is not None:
                    self._cache.put(cache_key, text)
                return response.json()
            if response.status_code in _RETRYABLE_STATUS and attempt < self.retries:
                retry_after = _parse_retry_after(response.headers.get("retry-after"))
                backoff = self._backoff(attempt, response.status_code)
                logger.warning(
                    "%s: HTTP %d for %s, retrying in %.0fs (attempt %d)",
                    self.source,
                    response.status_code,
                    path,
                    max(retry_after, backoff),
                    attempt + 1,
                )
                await asyncio.sleep(max(retry_after, backoff))
                continue
            raise FetchError(
                self.source,
                f"HTTP {response.status_code} for {url}",
                status_code=response.status_code,
            )
        raise FetchError(self.source, f"{last_error} for {url}")

    async def _throttle(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._last_request + self.min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = loop.time()

    def _backoff(self, attempt: int, status_code: int = 0) -> float:
        if status_code == 429:
            # Rate-limit windows can outlast short backoffs; wait longer.
            return min(self._backoff_429_base * 3.0**attempt, 60.0)  # 5s, 15s, 45s...
        return 2.0**attempt  # 1s, 2s, 4s

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> RateLimitedClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


def _parse_retry_after(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except ValueError:
        return 0.0
