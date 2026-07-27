"""
Populate a coarse stock-status label per product from a static stock export.

Writes a machine code into x_studio_stock_status (available | low | out) and the
snapshot date into x_studio_stock_as_of. A colour badge is rendered from these
by the website views (see tools/push_stock_badge_views.py); ordering is never
blocked — the badge is informational only.

Status rule (per the agreed thresholds):
  out        Available <= 0
  low        0 < Available < max(5, 50% of Min Stock)
  available  otherwise

Input: the Kerridge stock export (.xls) with columns (matched by header):
  Product ; Sales Price ; Max Stock ; Min Stock ; Available stock
Matched to Odoo by default_code (Kerger number).

Usage:
  python3 tools/update_stock_status.py --file STOCK_267.xls --dry-run
  python3 tools/update_stock_status.py --file STOCK_267.xls --apply
"""

import argparse
import datetime
import os
import sys
from collections import Counter

import xlrd

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

STATUS_FIELD = "x_studio_stock_status"
ASOF_FIELD = "x_studio_stock_as_of"
FLOOR = 5.0


def status_of(min_stock, avail):
    if avail <= 0:
        return "out"
    if avail < max(FLOOR, 0.5 * min_stock):
        return "low"
    return "available"


def num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def load(path):
    wb = xlrd.open_workbook(path)
    sh = wb.sheet_by_index(0)
    header = [str(sh.cell_value(0, cc)).strip().lower() for cc in range(sh.ncols)]

    def col(name, default):
        return header.index(name) if name in header else default
    i_code = col("product", 0)
    i_min = col("min stock", 3)
    i_av = col("available stock", 4)
    rows = []
    for r in range(1, sh.nrows):
        code = str(sh.cell_value(r, i_code)).strip()
        if not code:
            continue
        rows.append((code, num(sh.cell_value(r, i_min)), num(sh.cell_value(r, i_av))))
    return rows


def ensure_field(c, name, label):
    f = c._execute_kw("ir.model.fields", "search_read",
        [[["model", "=", "product.template"], ["name", "=", name]]], {"fields": ["id"]})
    if f:
        return f[0]["id"]
    model = c._execute_kw("ir.model", "search", [[["model", "=", "product.template"]]], {"limit": 1})[0]
    return c._execute_kw("ir.model.fields", "create", [{
        "name": name, "field_description": label, "model_id": model,
        "model": "product.template", "ttype": "char", "state": "manual"}], {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--as-of", help="snapshot date shown on the badge (YYYY-MM-DD); default today")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.file):
        sys.exit(f"ERROR: file not found: {args.file}")

    as_of = args.as_of or datetime.date.today().isoformat()
    rows = load(args.file)
    c = OdooClient(); c.authenticate()

    codes = [a for a, _, _ in rows]
    prods = c._execute_kw("product.template", "search_read",
        [[["default_code", "in", codes]]], {"fields": ["id", "default_code"]})
    byc = {str(p["default_code"]).strip(): p["id"] for p in prods}

    plan = {"available": [], "low": [], "out": []}
    unmatched = 0
    for code, mn, av in rows:
        if code not in byc:
            unmatched += 1
            continue
        plan[status_of(mn, av)].append(byc[code])

    matched = sum(len(v) for v in plan.values())
    print(f"file rows: {len(rows)} | matched: {matched} | unmatched (skipped): {unmatched}")
    print(f"status: available={len(plan['available'])} low={len(plan['low'])} out={len(plan['out'])}")
    print(f"as-of date on badge: {as_of}")

    if args.dry_run:
        print("\n(dry-run — nothing written)")
        return

    ensure_field(c, STATUS_FIELD, "Stock status")
    ensure_field(c, ASOF_FIELD, "Stock as of")
    for code, ids in plan.items():
        for i in range(0, len(ids), 200):
            c._execute_kw("product.template", "write",
                [ids[i:i+200], {STATUS_FIELD: code, ASOF_FIELD: as_of}], {})
    print(f"\nwrote status to {matched} products (as of {as_of}).")


if __name__ == "__main__":
    main()
