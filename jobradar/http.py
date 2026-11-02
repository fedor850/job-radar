"""Shared HTTP session with sane defaults, retries and a couple of helpers."""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15",
]

_local = threading.local()


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url
        self.body = body[:500]


def session() -> requests.Session:
    """One session per thread (requests.Session is not strictly thread-safe)."""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        retry = Retry(
            total=2,
            connect=2,
            read=1,
            backoff_factor=1.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers.update(
            {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
            }
        )
        _local.session = s
    return s


def _wait_429(r: requests.Response) -> float | None:
    """Seconds to wait before retrying after a 429: the server's Retry-After if it is short, else 8–15 s.
    None = the server asks for a long pause → do not retry now."""
    ra = (r.headers.get("Retry-After") or "").strip()
    if ra.isdigit():
        return float(ra) + random.uniform(1, 3) if int(ra) <= 40 else None
    return random.uniform(8, 15)


def request(method: str, url: str, *, timeout: float = 25, ok=(200,), retry_429: int = 1,
            **kw: Any) -> requests.Response:
    """`retry_429`: how often a 429 is repeated after a short pause (rate limits are usually per request and
    random; one patient retry saves most of them). LinkedIn passes 0: it has its own queue and pauses."""
    for attempt in range(max(retry_429, 0) + 1):
        t0 = time.monotonic()
        r = session().request(method, url, timeout=timeout, **kw)
        dt = time.monotonic() - t0
        if r.status_code == 429 and 429 not in ok and attempt < retry_429:
            wait = _wait_429(r)
            if wait is not None:
                log.info("HTTP 429 for %s — retry %d/%d in %.0f s", url, attempt + 1, retry_429, wait)
                time.sleep(wait)
                continue
        break
    if r.status_code not in ok:
        log.debug("HTTP %s %s -> %s (%.1fs)", method, url, r.status_code, dt)
        raise HttpError(r.status_code, url, r.text if r.text else "")
    return r


def get(url: str, **kw: Any) -> requests.Response:
    return request("GET", url, **kw)


def get_json(url: str, **kw: Any) -> Any:
    headers = {"Accept": "application/json", **kw.pop("headers", {})}
    return get(url, headers=headers, **kw).json()


def post_json(url: str, payload: Any, **kw: Any) -> Any:
    headers = {"Accept": "application/json", "Content-Type": "application/json", **kw.pop("headers", {})}
    return request("POST", url, json=payload, headers=headers, **kw).json()
