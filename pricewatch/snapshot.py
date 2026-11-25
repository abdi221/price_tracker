"""Save today's prices for every URL in the basket.

Usage:
    python -m pricewatch.snapshot basket.csv [--out data]

basket.csv is the Basket tab exported from the sheet (columns: Game, Shop, URL, Check).
Each run writes one file: data/<YYYY-MM-DD>/<HHMM>.json (Swedish time).

Rules: robots.txt is respected, 2 s between requests to the same shop,
honest User-Agent, and every failure is written to the file (never silently dropped).
Standard library only.
"""

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

CONTACT = "abdifatahali22@gmail.com" 
USER_AGENT = f"pricewatch/0.1 (+mailto:{CONTACT})"
DELAY_SECONDS = 2.0
TIMEOUT_SECONDS = 20
TZ = ZoneInfo("Europe/Stockholm")

_last_request: dict[str, float] = {}
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


# ---------- fetching ----------

def _wait_for(host: str) -> None:
    elapsed = time.monotonic() - _last_request.get(host, 0.0)
    if elapsed < DELAY_SECONDS:
        time.sleep(DELAY_SECONDS - elapsed)
    _last_request[host] = time.monotonic()


def _get(url: str) -> str:
    host = urlparse(url).netloc
    _wait_for(host)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "sv-SE,sv;q=0.9"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def robots_allows(url: str) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(_get(base + "/robots.txt").splitlines())
            _robots[base] = rp
        except urllib.error.HTTPError as e:
            # 4xx = no robots.txt = allowed; 5xx = be careful, treat as blocked
            _robots[base] = None if e.code < 500 else "blocked"  # type: ignore[assignment]
        except Exception:
            _robots[base] = "blocked"  # type: ignore[assignment]
    rp = _robots[base]
    if rp is None:
        return True
    if rp == "blocked":
        return False
    return rp.can_fetch(USER_AGENT, url)


# ---------- JSON-LD parsing ----------

_LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def _walk(node):
    """Yield every dict inside a JSON-LD blob (handles lists and @graph)."""
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                yield from _walk(node[key])


def _is_type(node: dict, name: str) -> bool:
    t = node.get("@type")
    return t == name or (isinstance(t, list) and name in t)


def _to_price(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace("\xa0", "").replace(" ", "")
    if "," in s and "." in s:          # 1.299,00 -> 1299.00
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _first_offer(offers):
    if isinstance(offers, list):
        return offers[0] if offers else {}
    return offers or {}


def parse_product(html: str) -> dict | None:
    for raw in _LD_RE.findall(html):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        for node in _walk(data):
            if not _is_type(node, "Product"):
                continue
            offer = _first_offer(node.get("offers"))
            price = offer.get("price") or offer.get("lowPrice")
            if price is None and isinstance(offer.get("priceSpecification"), dict):
                price = offer["priceSpecification"].get("price")
            availability = str(offer.get("availability", "")).rsplit("/", 1)[-1] or None
            gtin = next((node[k] for k in ("gtin13", "gtin", "gtin12", "gtin8", "ean") if node.get(k)), None)
            return {
                "name": node.get("name"),
                "price": _to_price(price),
                "currency": offer.get("priceCurrency"),
                "availability": availability,
                "gtin": str(gtin) if gtin else None,
                "sku": node.get("sku"),
            }
    return None


# ---------- main ----------

def read_basket(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    basket = []
    for r in rows:
        url = (r.get("URL") or "").strip()
        if url.startswith("http"):
            basket.append({"game": (r.get("Game") or "").strip(), "shop": (r.get("Shop") or "").strip(), "url": url})
    return basket


def snapshot_one(item: dict) -> dict:
    result = {**item, "fetched_at": datetime.now(TZ).isoformat(timespec="seconds"), "status": "ok", "error": None}
    try:
        if not robots_allows(item["url"]):
            return {**result, "status": "blocked", "error": "robots.txt disallows this URL"}
        product = parse_product(_get(item["url"]))
        if product is None:
            return {**result, "status": "error", "error": "no JSON-LD Product found on page"}
        if product["price"] is None:
            return {**result, **product, "status": "error", "error": "Product found but no price"}
        return {**result, **product}
    except urllib.error.HTTPError as e:
        return {**result, "status": "error", "error": f"HTTP {e.code}"}
    except Exception as e:  # network errors, timeouts, decoding problems
        return {**result, "status": "error", "error": f"{type(e).__name__}: {e}"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("basket", type=Path)
    ap.add_argument("--out", type=Path, default=Path("data"))
    args = ap.parse_args(argv)

    basket = read_basket(args.basket)
    if not basket:
        print(f"No URLs found in {args.basket}", file=sys.stderr)
        return 1

    # Interleave shops so the 2 s per-shop delay overlaps instead of adding up.
    by_shop: dict[str, list[dict]] = {}
    for item in basket:
        by_shop.setdefault(urlparse(item["url"]).netloc, []).append(item)
    ordered = []
    while any(by_shop.values()):
        for items in by_shop.values():
            if items:
                ordered.append(items.pop(0))

    results = []
    for i, item in enumerate(ordered, 1):
        r = snapshot_one(item)
        results.append(r)
        price = f"{r['price']:.0f} {r.get('currency') or ''}" if r.get("price") is not None else "-"
        print(f"[{i:>3}/{len(ordered)}] {r['status']:<7} {r['shop'][:15]:<15} {r['game'][:25]:<25} {price:<10} {r['error'] or ''}")

    now = datetime.now(TZ)
    out_file = args.out / now.strftime("%Y-%m-%d") / f"{now.strftime('%H%M')}.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(r["status"] == "ok" for r in results)
    print(f"\n{ok}/{len(results)} OK -> {out_file}")
    return 0 if ok else 2   # fail the run only if nothing worked


if __name__ == "__main__":
    sys.exit(main())