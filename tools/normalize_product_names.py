"""
Normalize product names on active products: collapse any run of whitespace
to a single space and trim ends. The Oct-2025 catalogue names carry doubled
spaces (e.g. "BALLAST   110V    20W  60HZ"); this tidies them to
"BALLAST 110V 20W 60HZ". Only records whose name actually changes are written.

Usage:
  python3 tools/normalize_product_names.py --dry-run
  python3 tools/normalize_product_names.py --apply
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

WS = re.compile(r"\s+")


def norm(name):
    return WS.sub(" ", name).strip() if name else name


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    c = OdooClient(); c.authenticate()
    recs = c._execute_kw("product.template", "search_read", [[]],
                         {"fields": ["id", "name"]})
    changes = [(r["id"], r["name"], norm(r["name"]))
               for r in recs if r["name"] and norm(r["name"]) != r["name"]]
    print(f"active products: {len(recs)} | names to normalize: {len(changes)}")
    for rid, old, new in changes[:25]:
        print(f"  {rid}: {old!r} -> {new!r}")
    if args.dry_run:
        print(f"  ... ({len(changes)} total)\n(dry-run — nothing written)")
        return
    done = 0
    for rid, _, new in changes:
        c._execute_kw("product.template", "write", [[rid], {"name": new}], {})
        done += 1
        if done % 100 == 0:
            print(f"  ...{done}/{len(changes)}")
    print(f"\ndone. normalized {done} names.")


if __name__ == "__main__":
    main()
