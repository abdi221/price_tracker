"""Command line: python -m pricewatch {detect,fetch,match,report} ..."""
from __future__ import annotations

import argparse
import sys

import requests

from pricewatch.detect import detect


def cmd_detect(args) -> int:
    r = detect(args.url)
    print(f"Shop: {r['base']}")
    print(f"  robots.txt   {r['robots']}")
    for name in ("shopify", "woocommerce", "jsonld"):
        ok, note = r[name]
        print(f"  {name:<12} {'YES' if ok else 'no':<4} {note}")
    print(f"Use: {r['use'] or 'NONE confirmed - see notes above'}")
    return 0 if r["use"] else 1


def not_built(args) -> int:
    print(f"'{args.command}' is not built yet.")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(prog="pricewatch")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("detect", help="which fetch method a shop supports")
    p.add_argument("url", help="shop URL (or a product page URL to test JSON-LD)")
    p.set_defaults(func=cmd_detect)

    for name in ("fetch", "match", "report"):
        sub.add_parser(name).set_defaults(func=not_built)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (requests.RequestException, ConnectionError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())