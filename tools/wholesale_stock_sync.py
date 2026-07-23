"""
Wholesale → Odoo stock sync (SCAFFOLD).

Pulls physical stock from the Wholesale REST API (`requestProductStock`),
matches products to Odoo by Internal Reference (default_code), and adjusts
Odoo on-hand quantity at a target location.

STATUS: scaffold. Two things must be resolved before it can apply:
  1. Target Odoo products must be STORABLE (is_storable=True) — consumable
     products cannot hold on-hand qty. All 460 products are currently non-storable.
  2. Confirm the Wholesale request/response schema from the Swagger page — the
     field names below (administrationCode, warehouseCode, productCodes,
     stockOverview…) are placeholders from the ChatGPT draft, NOT verified.

Config (all via .env, never hard-code the token):
  WHOLESALE_STOCK_URL=https://<server>/<path>/requestProductStock
  WHOLESALE_API_TOKEN=...
  WHOLESALE_ADMIN_CODE=20
  WHOLESALE_WAREHOUSE_CODE=CENTRAL
  ODOO_STOCK_LOCATION_ID=5           # WH/Stock today; a Houston location if created

Usage:
  python3 tools/wholesale_stock_sync.py --dry-run     # report diffs, write nothing
  python3 tools/wholesale_stock_sync.py --apply       # apply inventory adjustments
"""

import argparse
import os
import sys

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)


# --- Wholesale field names — CONFIRM against the Swagger before --apply ---
WS_FIELD_ADMIN = "administrationCode"
WS_FIELD_WAREHOUSE = "warehouseCode"
WS_FIELD_CODES = "productCodes"
WS_FIELD_PROPERTIES = "propertiesToInclude"
WS_PROPERTY_STOCK = "stockOverview"
# Which number in the response is "physical stock". Do NOT trust the label —
# verify against a known product's voorraadprognose first (per ChatGPT's note).
WS_RESPONSE_QTY_PATH = ("stockOverview", "physicalStock")


class WholesaleClient:
    """Adapter around requestProductStock. Finalize once the schema is confirmed."""

    def __init__(self):
        self.url = os.getenv("WHOLESALE_STOCK_URL", "")
        self.token = os.getenv("WHOLESALE_API_TOKEN", "")
        self.admin = os.getenv("WHOLESALE_ADMIN_CODE", "20")
        self.warehouse = os.getenv("WHOLESALE_WAREHOUSE_CODE", "CENTRAL")
        if not self.url or not self.token:
            sys.exit("Set WHOLESALE_STOCK_URL and WHOLESALE_API_TOKEN in .env")

    def fetch_stock(self, product_codes, batch=200):
        """Return {product_code: physical_qty} for the CENTRAL warehouse."""
        out = {}
        for i in range(0, len(product_codes), batch):
            chunk = product_codes[i:i + batch]
            payload = {
                WS_FIELD_ADMIN: self.admin,
                WS_FIELD_WAREHOUSE: self.warehouse,
                WS_FIELD_CODES: chunk,
                WS_FIELD_PROPERTIES: [WS_PROPERTY_STOCK],
            }
            r = requests.post(self.url, json=payload, timeout=60, headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            })
            r.raise_for_status()
            for row in _iter_products(r.json()):
                code, qty = _extract(row)
                if code is not None and qty is not None:
                    out[str(code)] = qty
        return out


def _iter_products(body):
    """Yield per-product rows from the response. Adjust to the real shape."""
    if isinstance(body, dict):
        return body.get("products") or body.get("items") or body.get("data") or []
    return body or []


def _extract(row):
    """Pull (product_code, physical_qty) from one response row. CONFIRM keys."""
    code = row.get("productCode") or row.get("cdprodukt") or row.get("code")
    node = row
    for key in WS_RESPONSE_QTY_PATH:
        node = (node or {}).get(key) if isinstance(node, dict) else None
    return code, (float(node) if node is not None else None)


class OdooStock:
    def __init__(self, location_id):
        self.c = OdooClient()
        self.c.authenticate()
        self.location_id = location_id

    def variant_map(self):
        """default_code -> product.product id (variants; catalogue has no real variants)."""
        recs = self.c._execute_kw("product.product", "search_read",
            [[["default_code", "!=", False]]],
            {"fields": ["id", "default_code", "is_storable"]})
        return {str(r["default_code"]).strip(): r for r in recs}

    def on_hand(self, product_ids):
        """Current on-hand qty at the target location, per product.product id."""
        quants = self.c._execute_kw("stock.quant", "search_read",
            [[["location_id", "=", self.location_id], ["product_id", "in", product_ids]]],
            {"fields": ["product_id", "quantity"]})
        return {q["product_id"][0]: q["quantity"] for q in quants}

    def set_qty(self, product_id, qty):
        """Inventory adjustment: set counted on-hand to `qty` at the location."""
        existing = self.c._execute_kw("stock.quant", "search",
            [[["product_id", "=", product_id], ["location_id", "=", self.location_id]]], {})
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
    g.add_argument("--dry-run", action="store_true", help="report diffs, write nothing")
    g.add_argument("--apply", action="store_true", help="apply inventory adjustments")
    args = ap.parse_args()

    loc = int(os.getenv("ODOO_STOCK_LOCATION_ID", "0"))
    if not loc:
        sys.exit("Set ODOO_STOCK_LOCATION_ID in .env (target location).")

    odoo = OdooStock(loc)
    vmap = odoo.variant_map()
    print(f"{len(vmap)} Odoo products with an internal reference")

    ws = WholesaleClient()
    ws_stock = ws.fetch_stock(list(vmap.keys()))
    print(f"{len(ws_stock)} stock figures returned by Wholesale")

    ids = [vmap[c]["id"] for c in ws_stock if c in vmap]
    current = odoo.on_hand(ids)

    changes, unmatched, non_storable = [], [], []
    for code, qty in ws_stock.items():
        rec = vmap.get(code)
        if not rec:
            unmatched.append(code)
            continue
        if not rec["is_storable"]:
            non_storable.append(code)
            continue
        now = current.get(rec["id"], 0.0)
        if now != qty:
            changes.append((rec["id"], code, now, qty))

    print(f"\nto change: {len(changes)} | unmatched: {len(unmatched)} | "
          f"non-storable (skipped): {len(non_storable)}")
    for _pid, code, now, qty in changes[:20]:
        print(f"  {code}: {now} -> {qty}")
    if unmatched:
        print("  unmatched Wholesale codes (sample):", unmatched[:10])
    if non_storable:
        print("  NON-STORABLE — make storable to sync (sample):", non_storable[:10])

    if args.apply:
        for pid, code, _now, qty in changes:
            odoo.set_qty(pid, qty)
        print(f"\napplied {len(changes)} inventory adjustment(s) at location {loc}")
    else:
        print("\n(dry-run — nothing written)")


if __name__ == "__main__":
    main()
