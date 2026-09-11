"""
Wholesale (Kerridge) -> Odoo stock sync, via ODBC.

Runs INSIDE the Wholesale/Citrix environment (that is the only place the DB
`030309-016-AP01:11501` is reachable). It:
  1. reads stock over ODBC  (SELECT code, qty  from lb-182 "Magazijnvoorraad"),
  2. matches products to Odoo by Internal Reference (default_code = Kerger nr),
  3. sets Odoo on-hand qty at a target location via an inventory adjustment,
pushing to Odoo over plain HTTPS (443) — no file hand-off, no cloud provider.

Configure everything in .env (never hard-code credentials):
  # --- Odoo (already set for this project) ---
  ODOO_URL=https://kerger.odoo.com
  ODOO_DB=kerger
  ODOO_USER=...
  ODOO_PASSWORD=...
  ODOO_STOCK_LOCATION_ID=14                 # HOU/Stock (target location)
  # --- Wholesale ODBC ---
  WHOLESALE_ODBC=DSN=Wholesale;UID=LK;PWD=...     # a DSN, or full driver string
  WHOLESALE_STOCK_SQL=SELECT <code_col>, <qty_col> FROM <lb-182 table> WHERE <warehouse filter>
  # the query MUST return exactly two columns: product_code, quantity

Usage (run these inside the environment):
  python wholesale_stock_sync.py --test-odbc     # show first rows the SQL returns
  python wholesale_stock_sync.py --test-odoo     # confirm Odoo is reachable
  python wholesale_stock_sync.py --dry-run       # report diffs, write nothing
  python wholesale_stock_sync.py --apply         # apply inventory adjustments
"""

import argparse
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)


# ------------------------- Wholesale (ODBC source) -------------------------
def fetch_stock():
    """Return {product_code: qty} by running WHOLESALE_STOCK_SQL over ODBC.
    The query must return two columns: product_code, quantity."""
    import pyodbc  # imported lazily: only needed on the Wholesale-side machine
    conn_str = os.getenv("WHOLESALE_ODBC", "")
    sql = os.getenv("WHOLESALE_STOCK_SQL", "")
    if not conn_str or not sql:
        sys.exit("Set WHOLESALE_ODBC and WHOLESALE_STOCK_SQL in .env")
    out = {}
    with pyodbc.connect(conn_str, timeout=60) as cx:
        cur = cx.cursor()
        cur.execute(sql)
        for row in cur.fetchall():
            code = row[0]
            qty = row[1]
            if code is None or qty is None:
                continue
            code = str(code).strip()
            try:
                out[code] = float(qty)
            except (TypeError, ValueError):
                continue
    return out


# ------------------------------ Odoo (target) ------------------------------
class OdooStock:
    def __init__(self, location_id):
        self.c = OdooClient(); self.c.authenticate()
        self.location_id = location_id

    def variant_map(self):
        """default_code -> product.product record (variant carrying the stock)."""
        recs = self.c._execute_kw("product.product", "search_read",
            [[["default_code", "!=", False]]],
            {"fields": ["id", "default_code", "is_storable"]})
        return {str(r["default_code"]).strip(): r for r in recs}

    def on_hand(self, product_ids):
        quants = self.c._execute_kw("stock.quant", "search_read",
            [[["location_id", "=", self.location_id],
              ["product_id", "in", product_ids]]],
            {"fields": ["product_id", "quantity"]})
        return {q["product_id"][0]: q["quantity"] for q in quants}

    def set_qty(self, product_id, qty):
        existing = self.c._execute_kw("stock.quant", "search",
            [[["product_id", "=", product_id],
              ["location_id", "=", self.location_id]]], {})
        if existing:
            self.c._execute_kw("stock.quant", "write",
                [existing, {"inventory_quantity": qty}], {})
            qid = existing
        else:
            qid = [self.c._execute_kw("stock.quant", "create",
                [{"product_id": product_id, "location_id": self.location_id,
                  "inventory_quantity": qty}], {})]
        self.c._execute_kw("stock.quant", "action_apply_inventory", [qid], {})


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--test-odbc", action="store_true",
                   help="run the SQL and print the first rows (no Odoo)")
    g.add_argument("--test-odoo", action="store_true",
                   help="confirm Odoo login + location (no Wholesale)")
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if args.test_odbc:
        stock = fetch_stock()
        print(f"ODBC OK — {len(stock)} rows. Sample:")
        for code, qty in list(stock.items())[:15]:
            print(f"  {code} -> {qty}")
        return

    loc = int(os.getenv("ODOO_STOCK_LOCATION_ID", "0"))
    if not loc:
        sys.exit("Set ODOO_STOCK_LOCATION_ID in .env (target location).")

    if args.test_odoo:
        odoo = OdooStock(loc)
        vmap = odoo.variant_map()
        print(f"Odoo OK — {len(vmap)} products with a Kerger number; "
              f"target location id {loc}.")
        return

    odoo = OdooStock(loc)
    vmap = odoo.variant_map()
    ws_stock = fetch_stock()
    print(f"{len(vmap)} Odoo products | {len(ws_stock)} Wholesale stock rows")

    ids = [vmap[c]["id"] for c in ws_stock if c in vmap]
    current = odoo.on_hand(ids)
    changes, unmatched, non_storable = [], [], []
    for code, qty in ws_stock.items():
        rec = vmap.get(code)
        if not rec:
            unmatched.append(code); continue
        if not rec["is_storable"]:
            non_storable.append(code); continue
        if current.get(rec["id"], 0.0) != qty:
            changes.append((rec["id"], code, current.get(rec["id"], 0.0), qty))

    print(f"to change: {len(changes)} | unmatched: {len(unmatched)} | "
          f"non-storable skipped: {len(non_storable)}")
    for _pid, code, now, qty in changes[:20]:
        print(f"  {code}: {now} -> {qty}")
    if unmatched:
        print("  unmatched Wholesale codes (sample):", unmatched[:10])
    if non_storable:
        print("  NON-STORABLE (make storable to sync, sample):", non_storable[:10])

    if args.apply:
        for pid, _code, _now, qty in changes:
            odoo.set_qty(pid, qty)
        print(f"\napplied {len(changes)} inventory adjustment(s) at location {loc}")
    else:
        print("\n(dry-run — nothing written)")


if __name__ == "__main__":
    main()
