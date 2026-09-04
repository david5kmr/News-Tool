"""HTTP-Schicht: ein Session-Objekt, hoefliche Defaults, Retries mit Backoff."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

import requests

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class Response:
    url: str
    status: int
    text: str
    content: bytes
    headers: Mapping[str, str]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300

    @property
    def not_modified(self) -> bool:
        return self.status == 304


class Fetcher:
    """Duenne requests-Huelle. Kein Parallelismus — die Quellenzahl ist klein
    und ein serieller Lauf ist fuer die Gegenseite freundlicher."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: int = 20,
        max_retries: int = 3,
        delay: float = 0.5,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
            }
        )
        self.timeout = timeout
        self.max_retries = max_retries
        self.delay = delay
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        accept: str | None = None,
    ) -> Response:
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        if accept:
            headers["Accept"] = accept

        last_error: str | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = self.session.get(
                    url, headers=headers, timeout=self.timeout, allow_redirects=True
                )
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code in RETRYABLE_STATUS and attempt < self.max_retries - 1:
                    last_error = f"HTTP {resp.status_code}"
                else:
                    return Response(
                        url=resp.url,
                        status=resp.status_code,
                        text=resp.text,
                        content=resp.content,
                        headers=dict(resp.headers),
                    )
            if attempt < self.max_retries - 1:
                time.sleep(2**attempt)

        return Response(
            url=url, status=0, text="", content=b"", headers={}, error=last_error
        )

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
