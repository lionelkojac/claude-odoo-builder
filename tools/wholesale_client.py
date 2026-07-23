"""
Wholesale ERP REST API client — reads calculated stock via `requestProductStock`.

This is a READ-ONLY client. It never writes to Wholesale.

    from wholesale_client import WholesaleClient
    ws = WholesaleClient()                      # reads config from .env
    rows = ws.get_stock(["123456", "234567"])   # -> [{"code": "123456", "qty": 12.0}, ...]

------------------------------------------------------------------------------
⚠️  UNCONFIRMED SCHEMA — READ THIS BEFORE TRUSTING THE NUMBERS
------------------------------------------------------------------------------
The request/response field names below are ILLUSTRATIVE placeholders taken from
ChatGPT's suggestion. They are almost certainly not exactly what your Wholesale
install uses. Wholesale returns SEVERAL stock concepts (physical / reserved /
free / incoming-from-PO / transfers). You must confirm two things before this
drives anything real:

  1. The exact request field names + casing   -> from your Swagger/WSDL.
  2. WHICH returned quantity you want          -> validate against a product
     whose stock you already know (compare to Wholesale's voorraadprognose).

Everything you need to change lives in the CONFIG block below. Nothing else in
this project needs editing when the real schema is known.

To confirm the mapping WITHOUT hitting the live API, save one real response to a
file and load it:

    ws = WholesaleClient.from_sample(".tmp/wholesale_sample.json")
    print(ws.get_stock(["123456"]))
------------------------------------------------------------------------------
"""

import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# =============================================================================
# CONFIG — the ONLY place to edit once you have the real Swagger/WSDL.
# =============================================================================

# --- Which fixed values to send (from CLAUDE.md / your Wholesale setup) ---
ADMINISTRATION_CODE = os.getenv("WHOLESALE_ADMINISTRATION", "20")
WAREHOUSE_CODE = os.getenv("WHOLESALE_WAREHOUSE", "CENTRAL")

# --- REQUEST field names.  Confirm casing against Swagger. ---
# Left = our internal name, Right = the exact JSON key Wholesale expects.
REQUEST_FIELDS = {
    "administration": "administrationCode",
    "warehouse": "warehouseCode",
    "product_codes": "productCodes",
    "properties": "propertiesToInclude",
    "include_delayed_po": "includeDelayedPurchaseOrders",
}
# Properties to request. "stockOverview" asks for the detailed breakdown.
REQUEST_PROPERTIES = ["stockOverview"]
# Keep incoming purchase orders OUT of the number, so we sync real on-hand.
INCLUDE_DELAYED_PURCHASE_ORDERS = False

# --- RESPONSE parsing.  Confirm against a real response. ---
# The list of product records may sit under a wrapper key, or be the top level.
# Candidate wrapper keys are tried in order; first list found wins.
RESPONSE_LIST_KEYS = ["products", "productStock", "results", "data", "items"]
# The key on each record holding the product code / internal reference.
RESPONSE_CODE_FIELD = "productCode"
# Candidate keys holding the quantity we want, tried in order. Put the concept
# you validated FIRST. Common candidates for "physical on-hand" shown here.
RESPONSE_QTY_FIELDS = [
    "physicalStock",
    "currentStock",
    "stockOverview.physical",   # dotted path is supported (see _dig)
    "quantity",
    "qty",
]

# =============================================================================
# End CONFIG
# =============================================================================


def _dig(record, dotted_key):
    """Fetch a possibly-nested value: _dig(rec, 'a.b') -> rec['a']['b']."""
    cur = record
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


class WholesaleClient:
    def __init__(self, base_url=None, token=None, _sample=None):
        self._sample = _sample  # when set, we never hit the network
        self.base_url = (base_url or os.getenv("WHOLESALE_STOCK_URL", "")).rstrip("/")
        self.token = token or os.getenv("WHOLESALE_API_TOKEN", "")

        if self._sample is None:
            if not self.base_url:
                sys.exit(
                    "ERROR: Missing Wholesale API URL. Set WHOLESALE_STOCK_URL in "
                    ".env (the full requestProductStock endpoint), or use "
                    "--sample-file for a dry mapping test."
                )
            if not self.token:
                sys.exit("ERROR: Missing WHOLESALE_API_TOKEN in .env.")

        self.session = requests.Session()

    @classmethod
    def from_sample(cls, path):
        """Build a client that returns a saved JSON response instead of calling."""
        with open(path, "r", encoding="utf-8") as f:
            return cls(_sample=json.load(f))

    # ------------------------------------------------------------------

    def _build_payload(self, product_codes):
        f = REQUEST_FIELDS
        return {
            f["administration"]: ADMINISTRATION_CODE,
            f["warehouse"]: WAREHOUSE_CODE,
            f["product_codes"]: list(product_codes),
            f["properties"]: REQUEST_PROPERTIES,
            f["include_delayed_po"]: INCLUDE_DELAYED_PURCHASE_ORDERS,
        }

    def _call(self, product_codes):
        """Return the raw decoded JSON from requestProductStock."""
        if self._sample is not None:
            return self._sample
        resp = self.session.post(
            self.base_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=self._build_payload(product_codes),
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def _extract_records(self, data):
        """Find the list of per-product records inside the response."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in RESPONSE_LIST_KEYS:
                if isinstance(data.get(key), list):
                    return data[key]
        raise ValueError(
            "Could not locate the product list in the Wholesale response. "
            f"Tried keys {RESPONSE_LIST_KEYS}. Update RESPONSE_LIST_KEYS in "
            "wholesale_client.py to match your real response shape. "
            f"Top-level keys seen: {list(data.keys()) if isinstance(data, dict) else type(data)}"
        )

    def parse(self, data):
        """Turn a raw response into [{'code': str, 'qty': float}], with report."""
        records = self._extract_records(data)
        rows, skipped = [], []
        for rec in records:
            code = _dig(rec, RESPONSE_CODE_FIELD) if "." in RESPONSE_CODE_FIELD \
                else rec.get(RESPONSE_CODE_FIELD)
            qty = None
            used_field = None
            for field in RESPONSE_QTY_FIELDS:
                val = _dig(rec, field)
                if val is not None:
                    qty, used_field = val, field
                    break
            if code is None or qty is None:
                skipped.append({"record": rec, "reason": "no code or qty field matched"})
                continue
            try:
                qty = float(qty)
            except (TypeError, ValueError):
                skipped.append({"record": rec, "reason": f"qty '{qty}' not numeric"})
                continue
            rows.append({"code": str(code).strip(), "qty": qty, "qty_field": used_field})
        return rows, skipped

    def get_stock(self, product_codes):
        """Convenience: call + parse, returning just the rows."""
        rows, _ = self.parse(self._call(product_codes))
        return rows


if __name__ == "__main__":
    # Smoke test: parse a sample file and print what we'd sync.
    import argparse

    ap = argparse.ArgumentParser(description="Test Wholesale stock parsing")
    ap.add_argument("--sample-file", help="A saved requestProductStock JSON response")
    ap.add_argument("--codes", help="Comma-separated product codes (live call)")
    args = ap.parse_args()

    if args.sample_file:
        wc = WholesaleClient.from_sample(args.sample_file)
        raw = wc._sample
    else:
        wc = WholesaleClient()
        codes = [c.strip() for c in (args.codes or "").split(",") if c.strip()]
        raw = wc._call(codes)

    parsed, skipped = wc.parse(raw)
    print(f"Parsed {len(parsed)} row(s):")
    for r in parsed[:50]:
        print(f"  {r['code']:>20}  qty={r['qty']:<12} (from '{r['qty_field']}')")
    if skipped:
        print(f"\n{len(skipped)} record(s) skipped (no code/qty match):")
        for s in skipped[:10]:
            print(f"  {s['reason']}: {json.dumps(s['record'])[:160]}")
