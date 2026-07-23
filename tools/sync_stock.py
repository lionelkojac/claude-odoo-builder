"""
Sync live stock from the Wholesale ERP into Odoo, matched by Internal Reference.

Reads calculated stock from Wholesale (`requestProductStock`), matches each SKU to
an Odoo product by `default_code` (Internal Reference), and records the quantity.

Two write modes:

  field      (DEFAULT, non-destructive)
             Writes the quantity to a custom field `x_wholesale_stock` on
             product.template plus a `x_wholesale_stock_synced` timestamp.
             No stock moves, no valuation entries. Use this when Wholesale is
             the system of record and Odoo just DISPLAYS the number.

  inventory  (opt-in, --mode inventory)
             Sets on-hand at an Odoo location via an inventory adjustment
             (stock.quant + action_apply_inventory). Only safe when Odoo does
             NOT independently deduct this product's stock. Requires the
             Inventory module and Inventory write rights.

ALWAYS dry-run first:
    python3 tools/sync_stock.py --dry-run --sample-file .tmp/wholesale_sample.json
    python3 tools/sync_stock.py --dry-run            # live Wholesale call
Then run for real:
    python3 tools/sync_stock.py --mode field
    python3 tools/sync_stock.py --mode inventory --location "Houston"

Inputs (.env):
    ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PASSWORD     (existing)
    WHOLESALE_STOCK_URL, WHOLESALE_API_TOKEN        (for live calls)
    WHOLESALE_ODOO_LOCATION  (optional, default "Houston" — inventory mode only)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient
from wholesale_client import WholesaleClient

STOCK_FIELD = "x_wholesale_stock"
SYNCED_FIELD = "x_wholesale_stock_synced"


# ---------------------------------------------------------------------------
# Odoo helpers
# ---------------------------------------------------------------------------

def ensure_custom_fields(client, dry_run):
    """Make sure x_wholesale_stock (float) + x_wholesale_stock_synced (char)
    exist on product.template. Returns True if both are present/created."""
    models = client.search("ir.model", [("model", "=", "product.template")], limit=1)
    if not models:
        sys.exit("ERROR: model product.template not found — is this an Odoo with products?")
    model_id = models[0]

    wanted = {
        STOCK_FIELD: ("Wholesale Live Stock", "float"),
        SYNCED_FIELD: ("Wholesale Stock Synced At", "char"),
    }
    for fname, (label, ttype) in wanted.items():
        exists = client.search(
            "ir.model.fields",
            [("model", "=", "product.template"), ("name", "=", fname)],
            limit=1,
        )
        if exists:
            continue
        if dry_run:
            print(f"  [dry-run] would create custom field product.template.{fname} ({ttype})")
            continue
        client.create(
            "ir.model.fields",
            {
                "name": fname,
                "field_description": label,
                "model_id": model_id,
                "model": "product.template",
                "ttype": ttype,
                "state": "manual",
            },
        )
        print(f"  created custom field product.template.{fname} ({ttype})")
    return True


def load_odoo_index(client):
    """Map Internal Reference -> {tmpl_id, variant_id, name, current}.
    Only products that HAVE an internal reference are indexed."""
    variants = client.search_read(
        "product.product",
        [("default_code", "!=", False)],
        ["default_code", "product_tmpl_id", "name"],
    )
    index = {}
    for v in variants:
        code = str(v["default_code"]).strip()
        tmpl = v["product_tmpl_id"]
        tmpl_id = tmpl[0] if isinstance(tmpl, (list, tuple)) else tmpl
        # First writer wins; duplicate internal refs are logged by the caller.
        index.setdefault(code, {
            "tmpl_id": tmpl_id,
            "variant_id": v["id"],
            "name": v["name"],
        })
    return index


def resolve_location(client, name):
    locs = client.search_read(
        "stock.location",
        [("name", "=", name), ("usage", "=", "internal")],
        ["id", "complete_name"],
    )
    if not locs:
        sys.exit(
            f"ERROR: No internal stock location named '{name}'. "
            "Check the exact name in Odoo > Inventory > Configuration > Locations, "
            "or pass --location."
        )
    if len(locs) > 1:
        opts = ", ".join(f"{l['complete_name']} (id {l['id']})" for l in locs)
        sys.exit(f"ERROR: Multiple locations named '{name}': {opts}. Disambiguate in Odoo.")
    return locs[0]["id"]


def set_on_hand(client, variant_id, location_id, qty):
    """Inventory adjustment: set on-hand of variant at location to qty."""
    quants = client.search(
        "stock.quant",
        [("product_id", "=", variant_id), ("location_id", "=", location_id)],
        limit=1,
    )
    if quants:
        quant_id = quants[0]
    else:
        quant_id = client.create(
            "stock.quant",
            {"product_id": variant_id, "location_id": location_id, "inventory_quantity": qty},
        )
    client.write("stock.quant", [quant_id], {"inventory_quantity": qty})
    client._execute_kw("stock.quant", "action_apply_inventory", [[quant_id]])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Sync Wholesale stock into Odoo")
    ap.add_argument("--mode", choices=["field", "inventory"], default="field",
                    help="field = custom display field (default, safe); "
                         "inventory = real inventory adjustment")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute and report changes without writing anything")
    ap.add_argument("--sample-file",
                    help="Use a saved requestProductStock JSON response instead of a live call")
    ap.add_argument("--location", default=os.getenv("WHOLESALE_ODOO_LOCATION", "Houston"),
                    help="Odoo internal location name (inventory mode only)")
    ap.add_argument("--limit-codes", type=int, default=0,
                    help="Cap number of Odoo SKUs requested from Wholesale (testing)")
    args = ap.parse_args()

    tmp_dir = os.path.join(os.path.dirname(__file__), "..", ".tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    client = OdooClient()

    # 1. Index Odoo products by internal reference.
    print("Indexing Odoo products by Internal Reference...")
    index = load_odoo_index(client)
    print(f"  {len(index)} product(s) with an Internal Reference.")
    if not index:
        sys.exit("Nothing to sync — no Odoo products have an Internal Reference set.")

    codes = list(index.keys())
    if args.limit_codes:
        codes = codes[: args.limit_codes]

    # 2. Fetch stock from Wholesale (live or sample).
    if args.sample_file:
        print(f"Reading Wholesale stock from sample file: {args.sample_file}")
        ws = WholesaleClient.from_sample(args.sample_file)
        rows, skipped = ws.parse(ws._sample)
    else:
        print(f"Requesting stock from Wholesale for {len(codes)} SKU(s)...")
        ws = WholesaleClient()
        rows, skipped = ws.parse(ws._call(codes))
    print(f"  Wholesale returned {len(rows)} usable row(s); {len(skipped)} unparseable.")

    # 3. Match + build a change plan.
    matched, unmatched, planned = [], [], []
    for row in rows:
        code, qty = row["code"], row["qty"]
        target = index.get(code)
        if not target:
            unmatched.append(row)
            continue
        matched.append({**row, **target})
        planned.append(
            f"  {code:>20}  {target['name'][:34]:<34}  -> {qty}"
        )

    print(f"\nMatched {len(matched)} / {len(rows)} Wholesale rows to Odoo products.")
    for line in planned[:50]:
        print(line)
    if len(planned) > 50:
        print(f"  ... and {len(planned) - 50} more")

    # 4. Apply (unless dry-run).
    print(f"\nMode: {args.mode}   Dry-run: {args.dry_run}")
    ensure_custom_fields(client, args.dry_run) if args.mode == "field" else None

    location_id = None
    if args.mode == "inventory":
        location_id = resolve_location(client, args.location)
        print(f"  Target location '{args.location}' -> id {location_id}")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    write_errors = []
    written = 0
    for m in matched:
        try:
            if args.dry_run:
                continue
            if args.mode == "field":
                client.write("product.template", [m["tmpl_id"]],
                             {STOCK_FIELD: m["qty"], SYNCED_FIELD: now})
            else:
                set_on_hand(client, m["variant_id"], location_id, m["qty"])
            written += 1
        except Exception as e:  # noqa: BLE001 — log per-SKU, keep going
            write_errors.append({"code": m["code"], "error": str(e)})

    # 5. Report + log.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = {
        "timestamp": stamp,
        "mode": args.mode,
        "dry_run": args.dry_run,
        "odoo_products_indexed": len(index),
        "wholesale_rows": len(rows),
        "matched": len(matched),
        "written": written,
        "unmatched_wholesale_skus": [r["code"] for r in unmatched],
        "unparseable_records": skipped,
        "write_errors": write_errors,
    }
    log_path = os.path.join(tmp_dir, f"stock_sync_{stamp}.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)

    print("\n" + "=" * 60)
    print(f"{'DRY-RUN — nothing written' if args.dry_run else 'SYNC COMPLETE'}")
    print(f"  matched:   {len(matched)}")
    print(f"  written:   {written}")
    print(f"  unmatched: {len(unmatched)} Wholesale SKU(s) not found in Odoo")
    print(f"  errors:    {len(write_errors)}")
    print(f"  log:       .tmp/{os.path.basename(log_path)}")
    if unmatched:
        print("\n  Unmatched Wholesale SKUs (first 20):")
        for r in unmatched[:20]:
            print(f"    {r['code']}  (qty {r['qty']})")
    if write_errors:
        print("\n  Write errors (first 10):")
        for e in write_errors[:10]:
            print(f"    {e['code']}: {e['error']}")


if __name__ == "__main__":
    main()
