"""
Correct product Sales Prices (list_price) from a price export CSV.

The current Odoo prices are shifted down by one row (each product holds the
price of the item listed above it — an off-by-one from an earlier import). This
tool rewrites list_price from a correctly-aligned export, matched by Kerger
number (default_code).

CSV format (semicolon-delimited, NL decimals):  Product;Omschrijving;Verkprijs

By default a NEW price of 0 is SKIPPED (treated as "not maintained in this
export"), so a real price is never wiped to zero; pass --include-zeros to apply
zeros too. Only products whose price actually changes are written.

Usage:
  python3 tools/update_prices_from_csv.py --file PRICELIST_ODOO.csv --dry-run
  python3 tools/update_prices_from_csv.py --file PRICELIST_ODOO.csv --apply
  python3 tools/update_prices_from_csv.py --file ... --apply --include-zeros
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

DIFF_OUT = os.path.join(os.path.dirname(__file__), "..", ".tmp", "price_diff.csv")


def parse_price(raw):
    raw = (raw or "").strip().replace(".", "").replace(",", ".")  # NL -> float
    try:
        return float(raw)
    except ValueError:
        return None


def load_csv(path):
    rows = []
    with open(path, encoding="utf-8-sig") as fh:
        r = csv.reader(fh, delimiter=";")
        next(r, None)  # header
        for x in r:
            if len(x) < 3 or not x[0].strip():
                continue
            rows.append((x[0].strip(), x[1].strip(), parse_price(x[2])))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--include-zeros", action="store_true",
                    help="also apply new prices of 0 (default: skip them)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        sys.exit(f"ERROR: file not found: {args.file}")

    rows = load_csv(args.file)
    c = OdooClient(); c.authenticate()
    codes = [code for code, _, _ in rows]
    prods = c._execute_kw("product.template", "search_read",
        [[["default_code", "in", codes]]],
        {"fields": ["id", "default_code", "name", "list_price"],
         "context": {"active_test": False}})
    byc = {str(p["default_code"]).strip(): p for p in prods}

    plan = []           # (id, code, name, current, new)
    skipped_zero = unmatched = unchanged = 0
    for code, desc, price in rows:
        if code not in byc:
            unmatched += 1
            continue
        if price is None:
            continue
        if price == 0 and not args.include_zeros:
            skipped_zero += 1
            continue
        p = byc[code]
        cur = p["list_price"] or 0
        if abs(cur - price) < 0.005:
            unchanged += 1
            continue
        plan.append((p["id"], code, p["name"], cur, price))

    print(f"CSV rows: {len(rows)} | matched to Odoo: "
          f"{sum(1 for code,_,_ in rows if code in byc)} | unmatched (skipped): {unmatched}")
    print(f"to update: {len(plan)} | unchanged: {unchanged} | "
          f"zero-price {'applied' if args.include_zeros else 'skipped'}: {skipped_zero}")

    # always write a full diff for review
    os.makedirs(os.path.dirname(DIFF_OUT), exist_ok=True)
    with open(DIFF_OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "name", "current_price", "new_price", "delta"])
        for _, code, name, cur, new in plan:
            w.writerow([code, name, f"{cur:.2f}", f"{new:.2f}", f"{new-cur:+.2f}"])
    print(f"full diff written to {os.path.relpath(DIFF_OUT)}")

    print("\nsample (code | name | current -> new):")
    for _, code, name, cur, new in plan[:15]:
        print(f"  {code:8} | {(name or '')[:34]:34} | {cur:8.2f} -> {new:.2f}")

    if args.dry_run:
        print("\n(dry-run — nothing written)")
        return

    # group by identical new price to minimise write calls
    groups = defaultdict(list)
    for pid, code, name, cur, new in plan:
        groups[round(new, 2)].append(pid)
    done = 0
    for price, ids in groups.items():
        c._execute_kw("product.template", "write", [ids, {"list_price": price}], {})
        done += len(ids)
    print(f"\napplied new prices to {done} products.")


if __name__ == "__main__":
    main()
