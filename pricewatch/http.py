"""One polite get(): honest User-Agent, robots.txt check, per-host delay.

Run the tool with `python -m pricewatch`, never `python pricewatch/__main__.py`:
the second form puts this folder on sys.path and this file would shadow
Python's own `http` module.
"""
from __future__ import annotations

import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

CONTACT = "abdifatahali22@gmail.com"  
USER_AGENT = f"pricewatch/0.1 (price comparison for small shops; contact: {CONTACT})"
DELAY_SECONDS = 2.0
TIMEOUT = 20

_session = requests.Session()
_session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.5",
})
_last_request: dict[str, float] = {}   # host -> time of last request
_host_delay: dict[str, float] = {}     # host -> delay (2 s, or robots Crawl-delay if longer)
_robots: dict[str, RobotFileParser] = {}
_robots_notes: dict[str, str] = {}


def _base(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc.lower()}"


def _raw_get(url: str) -> requests.Response:
    host = urlsplit(url).netloc.lower()
    last = _last_request.get(host)
    if last is not None:
        wait = _host_delay.get(host, DELAY_SECONDS) - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    try:
        return _session.get(url, timeout=TIMEOUT)
    finally:
        _last_request[host] = time.monotonic()


def _robots_for(url: str) -> RobotFileParser:
    base = _base(url)
    if base in _robots:
        return _robots[base]
    rp = RobotFileParser()
    resp = _raw_get(base + "/robots.txt")
    if resp.status_code in (401, 403):
        rp.disallow_all = True
        note = f"HTTP {resp.status_code} -> treated as 'disallow everything' (shop may block bots)"
    elif resp.status_code >= 500:
        raise ConnectionError(f"{base}/robots.txt returned HTTP {resp.status_code}; not fetching until it works")
    elif resp.status_code >= 400:
        rp.allow_all = True
        note = f"HTTP {resp.status_code} (no robots.txt) -> everything allowed"
    else:
        rp.parse(resp.text.splitlines())
        note = "found"
        crawl_delay = rp.crawl_delay(USER_AGENT)
        if crawl_delay and float(crawl_delay) > DELAY_SECONDS:
            _host_delay[urlsplit(base).netloc] = float(crawl_delay)
            note += f", Crawl-delay {crawl_delay} s respected"
    _robots[base] = rp
    _robots_notes[base] = note
    return rp


def robots_note(url: str) -> str:
    return _robots_notes.get(_base(url), "not fetched yet")


def get(url: str, params: dict | None = None) -> requests.Response:
    """Polite GET. Raises PermissionError if robots.txt disallows the URL.

    Returns the response whatever its status; callers decide what 404 means.
    """
    full = requests.Request("GET", url, params=params).prepare().url
    if not _robots_for(full).can_fetch(USER_AGENT, full):
        raise PermissionError(f"robots.txt disallows {full}")
    return _raw_get(full)


def blocked_reason(resp: requests.Response) -> str | None:
    """Return a reason if the response looks like bot blocking, else None."""
    if resp.headers.get("cf-mitigated") == "challenge":
        return "Cloudflare challenge page (needs a real browser) - swap this shop"
    if resp.status_code == 429:
        return "HTTP 429 Too Many Requests (rate limited)"
    if resp.status_code in (403, 503):
        return f"HTTP {resp.status_code} from server '{resp.headers.get('server', '?')}'"
    return None