"""Suggest product URLs for the basket's games in a new shop.

Usage:
    python -m pricewatch.find https://shop.se --name "Shop name" [--basket basket.csv] [--out candidates.csv]

Tries, in order: Shopify /products.json, the WooCommerce Store API search, then the
shop's sitemap(s). Matches against the game names in basket.csv and appends
candidates to candidates.csv as Game,Shop,URL,Check rows for you to review.

Same politeness rules as snapshot: robots.txt, 2 s between requests, honest User-Agent.
"""

import argparse
import csv
import json
import re
import sys
import urllib.error
from pathlib import Path
from urllib.parse import quote, urlparse
from xml.etree import ElementTree

from .snapshot import _get, robots_allows, read_basket

MAX_SITEMAPS = 25          # stop following sitemap indexes after this many files
MAX_SHOPIFY_PAGES = 20     # 20 x 250 = 5000 products

# Extra spellings shops use for some games (normalised form)
ALIASES = {
    "monopol": ["monopoly"],
    "smart10": ["smart 10"],
    "den forsvunna diamanten": ["forsvunna diamanten"],
}


def norm(text: str) -> str:
    text = text.lower()
    for a, b in (("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e"), ("%c3%a5", "a"), ("%c3%a4", "a"), ("%c3%b6", "o")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def patterns_for(game: str) -> list[re.Pattern]:
    key = norm(game)
    keys = [key] + ALIASES.get(key, [])
    return [re.compile(r"\b" + re.escape(k) + r"\b") for k in keys]


def match(games: dict[str, list[re.Pattern]], *texts: str) -> str | None:
    hay = " ".join(norm(t) for t in texts if t)
    for game, pats in games.items():
        if any(p.search(hay) for p in pats):
            return game
    return None


def fetch_text(url: str) -> str | None:
    if not robots_allows(url):
        print(f"  robots.txt disallows {url}", file=sys.stderr)
        return None
    try:
        return _get(url)
    except urllib.error.HTTPError as e:
        print(f"  {url} -> HTTP {e.code}", file=sys.stderr)
    except Exception as e:
        print(f"  {url} -> {type(e).__name__}", file=sys.stderr)
    return None


# ---------- source 1: Shopify ----------

def from_shopify(base: str, games) -> list[tuple[str, str, str]]:
    found = []
    for page in range(1, MAX_SHOPIFY_PAGES + 1):
        text = fetch_text(f"{base}/products.json?limit=250&page={page}")
        if not text:
            break
        try:
            products = json.loads(text).get("products", [])
        except (json.JSONDecodeError, AttributeError):
            break
        if not products:
            break
        for p in products:
            game = match(games, p.get("title", ""), p.get("handle", ""))
            if game:
                found.append((game, f"{base}/products/{p['handle']}", p.get("title", "")))
    return found


# ---------- source 2: WooCommerce Store API ----------

def from_woocommerce(base: str, games) -> list[tuple[str, str, str]]:
    found = []
    for game in games:
        text = fetch_text(f"{base}/wp-json/wc/store/v1/products?search={quote(game)}&per_page=20")
        if not text:
            if game == next(iter(games)):
                return []   # first search failed: probably not WooCommerce, don't keep trying
            continue
        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(items, list):
            return []
        for it in items:
            if match({game: games[game]}, it.get("name", ""), it.get("permalink", "")):
                found.append((game, it["permalink"], it.get("name", "")))
    return found


# ---------- source 3: sitemaps ----------

def _sitemap_starts(base: str) -> list[str]:
    starts = []
    robots = fetch_text(base + "/robots.txt") or ""
    for line in robots.splitlines():
        if line.lower().startswith("sitemap:"):
            starts.append(line.split(":", 1)[1].strip())
    return starts or [base + "/sitemap.xml", base + "/sitemap_index.xml"]


def _parse_sitemap(text: str) -> tuple[list[str], list[str]]:
    """Return (child sitemaps, page urls)."""
    try:
        root = ElementTree.fromstring(text.encode("utf-8"))
    except ElementTree.ParseError:
        return [], []
    locs = [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]
    if root.tag.endswith("sitemapindex"):
        return locs, []
    return [], locs


def from_sitemaps(base: str, games) -> list[tuple[str, str, str]]:
    queue, seen, found = _sitemap_starts(base), set(), []
    while queue and len(seen) < MAX_SITEMAPS:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        if sm.endswith(".gz"):
            print(f"  skipping compressed sitemap {sm}", file=sys.stderr)
            continue
        text = fetch_text(sm)
        if not text:
            continue
        children, pages = _parse_sitemap(text)
        # Product sitemaps first; skip obvious non-product ones (blog, images, pages)
        children.sort(key=lambda u: 0 if "product" in u.lower() else 1)
        queue.extend(c for c in children if not re.search(r"blog|post|image|page-sitemap|author|tag", c, re.I))
        for url in pages:
            game = match(games, urlparse(url).path)
            if game:
                found.append((game, url, ""))
    return found


# ---------- main ----------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shop_url")
    ap.add_argument("--name", required=True, help="shop name as it should appear in the basket")
    ap.add_argument("--basket", type=Path, default=Path("basket.csv"))
    ap.add_argument("--out", type=Path, default=Path("candidates.csv"))
    args = ap.parse_args(argv)

    p = urlparse(args.shop_url)
    base = f"{p.scheme or 'https'}://{p.netloc or p.path}".rstrip("/")
    game_names = sorted({row["game"] for row in read_basket(args.basket)})
    games = {g: patterns_for(g) for g in game_names}

    found, source = [], None
    for name, fn in (("shopify", from_shopify), ("woocommerce", from_woocommerce), ("sitemap", from_sitemaps)):
        print(f"Trying {name}...", file=sys.stderr)
        found = fn(base, games)
        if found:
            source = name
            break

    # de-duplicate by URL
    unique = list({url: (game, url, title) for game, url, title in found}.values())
    unique.sort(key=lambda r: game_names.index(r[0]))

    new_file = not args.out.exists()
    with args.out.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["Game", "Shop", "URL", "Check"])
        for game, url, title in unique:
            w.writerow([game, args.name, url, f"candidate ({source}){': ' + title if title else ''}"])

    print(f"\n{args.name}: {len(unique)} candidate(s) via {source or 'nothing'}")
    for game, url, title in unique:
        print(f"  {game:<25} {url}")
    missing = [g for g in game_names if g not in {r[0] for r in unique}]
    if missing:
        print(f"  not found: {', '.join(missing)}")
    print(f"-> appended to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())