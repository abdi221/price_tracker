"""The common product record every fetcher returns."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Product:
    shop: str                 # shop base URL, e.g. https://example.se
    title: str
    sku: str | None
    ean: str | None           # EAN/GTIN digits only (validated), or None
    price_sek: float | None   # None = price missing -> report must flag it
    in_stock: bool | None     # None = shop didn't say
    url: str
    fetched_at: str           # ISO 8601, UTC


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_ean(value) -> str | None:
    """Return EAN/GTIN as digits if it is a valid GTIN-8/12/13/14, else None.

    The check digit catches junk values shops sometimes put in the gtin field,
    which would otherwise create false "exact" matches.
    """
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) not in (8, 12, 13, 14):
        return None
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return digits if (10 - total % 10) % 10 == check else None