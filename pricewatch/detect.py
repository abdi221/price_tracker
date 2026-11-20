"""Work out which of the three methods a shop supports."""
from __future__ import annotations

from urllib.parse import urlsplit

from pricewatch.http import blocked_reason, get, robots_note
from pricewatch.jsonld import extract_blocks, node_types


def base_url(url: str) -> str:
    if "://" not in url:
        url = "https://" + url
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc.lower()}"


def _probe(url: str, params: dict | None = None):
    """GET url. Returns (response or None, note)."""
    try:
        resp = get(url, params)
    except PermissionError as e:
        return None, f"skipped: {e}"
    reason = blocked_reason(resp)
    if reason:
        return None, f"BLOCKED: {reason}"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    return resp, "HTTP 200"


def _json(resp):
    try:
        return resp.json()
    except ValueError:
        return None


def check_shopify(base: str) -> tuple[bool, str]:
    resp, note = _probe(base + "/products.json", {"limit": 1})
    if resp is None:
        return False, note
    data = _json(resp)
    if isinstance(data, dict) and isinstance(data.get("products"), list):
        return True, "/products.json works"
    return False, f"HTTP 200 but not Shopify JSON ({resp.headers.get('content-type', '?')})"


def check_woocommerce(base: str) -> tuple[bool, str]:
    resp, note = _probe(base + "/wp-json/wc/store/v1/products", {"per_page": 1})
    if resp is None:
        return False, note
    data = _json(resp)
    if isinstance(data, list) and (not data or "prices" in data[0]):
        return True, "Store API works"
    return False, f"HTTP 200 but not Store API JSON ({resp.headers.get('content-type', '?')})"


def check_jsonld(page_url: str) -> tuple[bool, str]:
    resp, note = _probe(page_url)
    if resp is None:
        return False, note
    blocks, broken = extract_blocks(resp.text)
    types = node_types(blocks)
    note = f"types on this page: {', '.join(types) or 'none'}"
    if broken:
        note += f" ({broken} broken block(s))"
    if "Product" not in types and urlsplit(page_url).path in ("", "/"):
        note += " - homepages rarely have Product markup; rerun with a product page URL"
    return "Product" in types, note


def detect(url: str) -> dict:
    """Return {'base', 'robots', 'shopify', 'woocommerce', 'jsonld', 'use'}.

    Each method maps to (supported, note). 'use' is the best supported method
    (shopify > woocommerce > jsonld) or None.
    """
    if "://" not in url:
        url = "https://" + url
    base = base_url(url)
    result = {
        "base": base,
        "shopify": check_shopify(base),
        "woocommerce": check_woocommerce(base),
        "jsonld": check_jsonld(url),
    }
    result["robots"] = robots_note(base)
    result["use"] = next(
        (m for m in ("shopify", "woocommerce", "jsonld") if result[m][0]), None
    )
    return result