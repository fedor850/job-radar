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


def reset_session() -> None:
    """Drop this thread's session: the next request gets a new connection, cookies and a different User-Agent."""
    _local.session = None


def _wait_429(r: requests.Response) -> float | None:
    """Seconds to wait before retrying after a 429: the server's Retry-After if it is short, else 8–15 s.
    None = the server asks for a long pause → do not retry now."""
    ra = (r.headers.get("Retry-After") or "").strip()
    if ra.isdigit():
        return float(ra) + random.uniform(1, 3) if int(ra) <= 40 else None
    return random.uniform(8, 15)


#: connection-level failures that usually vanish on the next try (timeouts, reset connections, half-read bodies)
TRANSIENT_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                    requests.exceptions.ChunkedEncodingError, requests.exceptions.ContentDecodingError)
#: gateway / origin errors (also Cloudflare's 52x) — worth exactly one more try
RETRY_5XX = frozenset({500, 502, 503, 504, 520, 521, 522, 523, 524})


def request(method: str, url: str, *, timeout: float = 25, ok=(200,), retry_429: int = 1, retry_net: int = 1,
            retry_5xx: int = 1, retry_block: tuple = (), **kw: Any) -> requests.Response:
    """One HTTP request that survives the usual one-off failures of scraped sites.

    retry_429  – how often a 429 is repeated after a short pause (rate limits are usually per request and random).
                 LinkedIn passes 0: it has its own queue and pauses.
    retry_net  – repeats after a timeout / dropped connection / half-read body.
    retry_5xx  – repeats after a 5xx from a gateway or the origin (a restarting server).
    retry_block – statuses such as 403 / 202 that anti-bot layers return at random: ONE repeat with a brand-new
                 session (new User-Agent and cookies) after a pause. Not used for statuses listed in `ok`.
    """
    left = {"429": max(retry_429, 0), "net": max(retry_net, 0), "5xx": max(retry_5xx, 0),
            "block": 1 if retry_block else 0}
    while True:
        t0 = time.monotonic()
        try:
            r = session().request(method, url, timeout=timeout, **kw)
        except TRANSIENT_ERRORS as e:
            if left["net"] <= 0:
                raise
            left["net"] -= 1
            wait = random.uniform(2, 5)
            log.info("%s for %s — retry in %.0f s", type(e).__name__, url, wait)
            time.sleep(wait)
            reset_session()
            continue
        dt = time.monotonic() - t0
        sc = r.status_code
        if sc in ok:
            return r
        if sc == 429 and left["429"] > 0:
            left["429"] -= 1
            wait = _wait_429(r)
            if wait is not None:
                log.info("HTTP 429 for %s — retry in %.0f s", url, wait)
                time.sleep(wait)
                continue
        elif sc in RETRY_5XX and left["5xx"] > 0:
            left["5xx"] -= 1
            wait = random.uniform(3, 7)
            log.info("HTTP %s for %s — retry in %.0f s", sc, url, wait)
            time.sleep(wait)
            continue
        elif sc in retry_block and left["block"] > 0:
            left["block"] -= 1
            wait = random.uniform(4, 9)
            log.info("HTTP %s for %s — retry with a fresh session in %.0f s", sc, url, wait)
            time.sleep(wait)
            reset_session()
            continue
        log.debug("HTTP %s %s -> %s (%.1fs)", method, url, sc, dt)
        raise HttpError(sc, url, r.text if r.text else "")


def get(url: str, **kw: Any) -> requests.Response:
    return request("GET", url, **kw)


def _json_twice(call) -> Any:
    """An empty or HTML body instead of JSON is how overloaded APIs (Workday, JOIN) say 'not now' → one more try."""
    try:
        return call().json()
    except ValueError:
        time.sleep(random.uniform(3, 6))
        return call().json()


def get_json(url: str, **kw: Any) -> Any:
    headers = {"Accept": "application/json", **kw.pop("headers", {})}
    return _json_twice(lambda: get(url, headers=headers, **kw))


def post_json(url: str, payload: Any, **kw: Any) -> Any:
    headers = {"Accept": "application/json", "Content-Type": "application/json", **kw.pop("headers", {})}
    return _json_twice(lambda: request("POST", url, json=payload, headers=headers, **kw))
