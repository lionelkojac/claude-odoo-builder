"""
Clear barcodes that are just the Kerger number.

Some products were imported with barcode == default_code (the Kerger internal
reference). That is not a real (EAN) barcode, so this clears it. Products with
a genuine barcode different from their Kerger number are left untouched.
Covers active and archived products.

Usage:
  python3 tools/clear_kerger_barcodes.py --dry-run
  python3 tools/clear_kerger_barcodes.py --apply
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    c = OdooClient(); c.authenticate()
    recs = c._execute_kw("product.template", "search_read",
        [[["barcode", "!=", False]]],
        {"fields": ["id", "default_code", "barcode"],
         "context": {"active_test": False}})
    hit = [r["id"] for r in recs
           if r["default_code"]
           and str(r["barcode"]).strip() == str(r["default_code"]).strip()]
    print(f"{len(recs)} products with a barcode | "
          f"{len(hit)} where barcode == Kerger number")

    if args.dry_run:
        print("(dry-run — nothing cleared)")
        return
    done = 0
    for i in range(0, len(hit), 200):
        chunk = hit[i:i + 200]
        c._execute_kw("product.template", "write",
                      [chunk, {"barcode": False}], {})
        done += len(chunk)
        print(f"  ...cleared {done}/{len(hit)}")
    print(f"\ndone. cleared {done} barcodes.")


if __name__ == "__main__":
    main()
