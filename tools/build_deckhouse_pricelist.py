"""
Build the Deckhouse (DCK) customer pricelist from an agreed-price CSV.

The pricelist = per-product AGREED prices (fixed) + one GLOBAL fallback rule of
18% off the Sales Price. Odoo applies the most specific matching rule first, so
listed products get their agreed price and everything else gets 18% off.

CSV (semicolon-delimited, NL decimals). Columns detected by header; recognised:
  Product ; Description ; Code pricelist ; Price ; [Date start] ; [Date end] ; ...

Only VALID prices are imported: price > 0 AND (if the file gives dates) the
date window covers today. Each rule is stamped with its start/end dates, so
Odoo enforces the window and auto-expires it. Because --apply first wipes DCK's
existing rules, any price previously loaded that is not in the new valid set is
removed (the product falls back to the 18% rule).

Usage:
  python3 tools/build_deckhouse_pricelist.py --file ... --dry-run
  python3 tools/build_deckhouse_pricelist.py --file ... --apply
"""

import argparse
import csv
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

PRICELIST_NAME = "DCK"
CUSTOMER_NAME = "Deckhouse Inc"
FALLBACK_DISCOUNT = 18.0   # percent off list_price for non-listed items
TODAY = datetime.date.today()


def parse_price(raw):
    raw = (raw or "").strip().replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        d, m, y = raw.split("-")
        return datetime.date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def load(path):
    """Return [(code, desc, price, date_start, date_end)] using header names."""
    with open(path, encoding="utf-8-sig") as fh:
        r = csv.reader(fh, delimiter=";")
        header = [h.strip().lower() for h in next(r, [])]
        idx = {name: header.index(name) for name in header}
        i_code = idx.get("product", 0)
        i_desc = idx.get("description", 1)
        i_price = idx.get("price", 3)
        i_ds = idx.get("date start")
        i_de = idx.get("date end")
        rows = []
        for x in r:
            if len(x) <= i_price or not x[i_code].strip():
                continue
            ds = parse_date(x[i_ds]) if i_ds is not None and len(x) > i_ds else None
            de = parse_date(x[i_de]) if i_de is not None and len(x) > i_de else None
            rows.append((x[i_code].strip(), x[i_desc].strip(),
                         parse_price(x[i_price]), ds, de))
    return rows


def is_valid(price, ds, de):
    if price in (None, 0):
        return False
    if ds and TODAY < ds:
        return False
    if de and TODAY > de:
        return False
    return True


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
    valid_rows = [r for r in rows if is_valid(r[2], r[3], r[4])]
    invalid = len(rows) - len(valid_rows)
    codes = [r[0] for r in valid_rows]
    # match to ACTIVE products (a pricelist rule should point at the live product)
    prods = c._execute_kw("product.template", "search_read",
        [[["default_code", "in", codes]]], {"fields": ["id", "default_code", "name", "list_price"]})
    byc = {str(p["default_code"]).strip(): p for p in prods}

    items, anomalies, unmatched = [], [], []
    for code, desc, price, ds, de in valid_rows:
        if code not in byc:
            unmatched.append(code)
            continue
        p = byc[code]
        vals = {"pricelist_id": plid, "applied_on": "1_product",
                "product_tmpl_id": p["id"], "compute_price": "fixed",
                "fixed_price": round(price, 2), "min_quantity": 0}
        if ds:
            vals["date_start"] = f"{ds.isoformat()} 00:00:00"
        if de:
            vals["date_end"] = f"{de.isoformat()} 23:59:59"
        items.append(vals)
        lp = p["list_price"] or 0
        if lp and price > lp + 0.005:
            anomalies.append((code, p["name"], lp, price))

    print(f"CSV rows: {len(rows)} | valid (in-date, price>0): {len(valid_rows)} | "
          f"invalid skipped: {invalid}")
    print(f"agreed-price rules to create: {len(items)} | unmatched (skipped): {len(unmatched)}")
    print(f"+ 1 global fallback rule: {FALLBACK_DISCOUNT:.0f}% off Sales Price")
    print("(a full rebuild — any current DCK price not in this valid set is removed)")
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
