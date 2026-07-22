"""
Make the Odoo shop search match on IMPA codes.

Odoo Online (SaaS) can't install a module to extend the product search, and the
IMPA value lives in a Studio field (x_studio_impa) that the shop search does not
index. This tool mirrors each product's IMPA into the internal `description`
field — which the shop search DOES index but which is not shown on the website —
so a customer searching an IMPA code finds the product.

Idempotent: the mirrored value is wrapped in a marked <p class="o_impa_search">
paragraph, so re-running updates/removes it cleanly without touching any other
description content. Run again after catalogue imports or IMPA edits.

Usage:
    python3 tools/sync_impa_search.py            # apply
    python3 tools/sync_impa_search.py --dry-run  # report only
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

MARKER_RE = re.compile(r'<p class="o_impa_search">.*?</p>', re.DOTALL)


def desired_description(current, impa):
    """Return description with the IMPA marker block set to `impa` (or removed)."""
    base = MARKER_RE.sub("", current or "").strip()
    if impa:
        block = f'<p class="o_impa_search">IMPA: {impa}</p>'
        return (base + block) if base else block
    return base or False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    c = OdooClient()
    c.authenticate()

    # Products that need a mirror (have IMPA) OR already carry a stale marker
    # (IMPA cleared → marker must be removed).
    ids = set(c._execute_kw("product.template", "search",
                            [[["x_studio_impa", "!=", False]]], {}))
    ids |= set(c._execute_kw("product.template", "search",
                             [[["description", "ilike", "o_impa_search"]]], {}))
    ids = list(ids)

    recs = c._execute_kw("product.template", "read", [ids],
                         {"fields": ["name", "description", "x_studio_impa"]})

    changed = 0
    for r in recs:
        impa = (r.get("x_studio_impa") or "").strip()
        current = r.get("description") or ""
        target = desired_description(current, impa)
        if (target or "") != (current or ""):
            changed += 1
            if args.dry_run:
                print(f"  [{r['id']}] {r['name'][:40]:<40} IMPA={impa or '(clear)'}")
            else:
                c._execute_kw("product.template", "write", [[r["id"]], {"description": target}], {})

    verb = "would update" if args.dry_run else "updated"
    print(f"{verb} {changed} product(s); {len(recs)} inspected.")


if __name__ == "__main__":
    main()
