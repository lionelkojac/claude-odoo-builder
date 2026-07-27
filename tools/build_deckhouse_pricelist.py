"""
Build the Deckhouse (DCK) customer pricelist from an agreed-price CSV.

The pricelist = per-product AGREED prices (fixed) + one GLOBAL fallback rule of
18% off the Sales Price. Odoo applies the most specific matching rule first, so
listed products get their agreed price and everything else gets 18% off.

CSV (semicolon-delimited, NL decimals):  Product;Description;Code pricelist;Price

Idempotent: on --apply it first removes DCK's existing rules, then recreates the
agreed-price rules (batched) + the 18% global rule, and assigns the pricelist to
the Deckhouse customer.

Usage:
  python3 tools/build_deckhouse_pricelist.py --file ... --dry-run
  python3 tools/build_deckhouse_pricelist.py --file ... --apply
"""

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

PRICELIST_NAME = "DCK"
CUSTOMER_NAME = "Deckhouse Inc"
FALLBACK_DISCOUNT = 18.0   # percent off list_price for non-listed items


def parse_price(raw):
    raw = (raw or "").strip().replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def load(path):
    rows = []
    with open(path, encoding="utf-8-sig") as fh:
        r = csv.reader(fh, delimiter=";")
        next(r, None)
        for x in r:
            if len(x) < 4 or not x[0].strip():
                continue
            rows.append((x[0].strip(), x[1].strip(), parse_price(x[3])))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.file):
        sys.exit(f"ERROR: file not found: {args.file}")

    c = OdooClient(); c.authenticate()
    pl = c._execute_kw("product.pricelist", "search_read",
                       [[["name", "=", PRICELIST_NAME]]], {"fields": ["id", "currency_id"]})
    if not pl:
        sys.exit(f"ERROR: pricelist '{PRICELIST_NAME}' not found.")
    plid = pl[0]["id"]

    rows = load(args.file)
    codes = [a for a, _, _ in rows]
    # match to ACTIVE products (a pricelist rule should point at the live product)
    prods = c._execute_kw("product.template", "search_read",
        [[["default_code", "in", codes]]], {"fields": ["id", "default_code", "name", "list_price"]})
    byc = {str(p["default_code"]).strip(): p for p in prods}

    items, anomalies, unmatched = [], [], []
    for code, desc, price in rows:
        if price in (None, 0):
            continue
        if code not in byc:
            unmatched.append(code)
            continue
        p = byc[code]
        items.append({"pricelist_id": plid, "applied_on": "1_product",
                      "product_tmpl_id": p["id"], "compute_price": "fixed",
                      "fixed_price": round(price, 2), "min_quantity": 0})
        lp = p["list_price"] or 0
        if lp and price > lp + 0.005:
            anomalies.append((code, p["name"], lp, price))

    print(f"CSV rows: {len(rows)} | agreed-price rules to create: {len(items)} | "
          f"unmatched (skipped): {len(unmatched)}")
    print(f"+ 1 global fallback rule: {FALLBACK_DISCOUNT:.0f}% off Sales Price")
    if anomalies:
        print(f"\n{len(anomalies)} agreed prices ABOVE the catalog price (please review):")
        for code, nm, lp, pr in anomalies:
            print(f"  {code:8} | {(nm or '')[:34]:34} | list {lp:.2f} < agreed {pr:.2f}")

    if args.dry_run:
        print("\nsample rules (code | name | agreed):")
        cur = {str(p["default_code"]).strip(): p for p in prods}
        for it in items[:10]:
            nm = next((p["name"] for p in prods if p["id"] == it["product_tmpl_id"]), "")
            print(f"  {it['fixed_price']:8.2f}  <- {(nm or '')[:40]}")
        print("\n(dry-run — nothing written)")
        return

    # wipe existing DCK rules, then rebuild
    old = c._execute_kw("product.pricelist.item", "search",
                        [[["pricelist_id", "=", plid]]], {})
    if old:
        c._execute_kw("product.pricelist.item", "unlink", [old], {})
        print(f"removed {len(old)} existing rule(s) from {PRICELIST_NAME}")

    created = 0
    for i in range(0, len(items), 200):
        c._execute_kw("product.pricelist.item", "create", [items[i:i+200]], {})
        created += len(items[i:i+200])
    # global fallback
    c._execute_kw("product.pricelist.item", "create", [{
        "pricelist_id": plid, "applied_on": "3_global",
        "compute_price": "percentage", "percent_price": FALLBACK_DISCOUNT,
        "base": "list_price", "min_quantity": 0}], {})
    print(f"created {created} agreed-price rules + 1 global {FALLBACK_DISCOUNT:.0f}% rule")

    # assign to the customer
    cust = c._execute_kw("res.partner", "search",
                         [[["name", "=", CUSTOMER_NAME]]], {"limit": 1})
    if cust:
        c._execute_kw("res.partner", "write",
                      [cust, {"property_product_pricelist": plid}], {})
        print(f"assigned '{PRICELIST_NAME}' pricelist to {CUSTOMER_NAME} (id {cust[0]})")
    else:
        print(f"WARNING: customer '{CUSTOMER_NAME}' not found — assign the pricelist manually.")


if __name__ == "__main__":
    main()
