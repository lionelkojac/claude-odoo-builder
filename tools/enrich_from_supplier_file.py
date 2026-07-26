"""
Enrich Odoo products from the Schneider/Eaton/Siemens supplier file.

The xlsx (Blad1) columns: Kerger nr | EAN | Brand | Image URL (2BA) | mfr code.
Matched to Odoo by default_code (Kerger number). Modes (combine freely):

  --brand      set the Brand attribute (id 9, no_variant) -> Brand shop filter
  --barcodes   set barcode = EAN  (valid 8/12-14 digit, unique; empty only)
  --images     download the 2BA image into image_1920 (only where missing)

Never overwrites an existing barcode/image; skips in-file duplicate EANs.

Usage:
  python3 tools/enrich_from_supplier_file.py --brand --dry-run
  python3 tools/enrich_from_supplier_file.py --brand --apply
  python3 tools/enrich_from_supplier_file.py --barcodes --images --apply
"""

import argparse
import base64
import os
import re
import sys
from collections import Counter

import openpyxl
import requests

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient
from build_product_filters import get_or_create_attribute, get_or_create_value

XLSX = ".tmp/control_data.xlsx"
BRAND_ATTR_NAME = "Brand"


def load_rows():
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    out = []
    for r in list(wb["Blad1"].iter_rows(values_only=True))[1:]:
        if r[0] in (None, ""):
            continue
        out.append({
            "kerger": str(r[0]).strip(),
            "ean": str(r[1]).strip() if r[1] else "",
            "brand": str(r[2]).strip() if r[2] else "",
            "img": str(r[3]).strip() if r[3] and str(r[3]).startswith("http") else "",
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", action="store_true")
    ap.add_argument("--barcodes", action="store_true")
    ap.add_argument("--images", action="store_true")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not (args.brand or args.barcodes or args.images):
        ap.error("choose at least one of --brand / --barcodes / --images")

    c = OdooClient(); c.authenticate()
    rows = load_rows()
    codes = [d["kerger"] for d in rows]
    prods = c._execute_kw("product.template", "search_read",
        [[["default_code", "in", codes]]],
        {"fields": ["id", "default_code", "barcode", "image_1920"],
         "context": {"active_test": False}})
    byc = {str(p["default_code"]).strip(): p for p in prods}
    matched = [d for d in rows if d["kerger"] in byc]
    print(f"{len(rows)} file rows | {len(matched)} matched to Odoo")

    # ---- Brand ----
    if args.brand:
        attr = get_or_create_attribute(c, BRAND_ATTR_NAME, args.dry_run)
        lines = {}
        if attr:
            for l in c._execute_kw("product.template.attribute.line", "search_read",
                    [[["attribute_id", "=", attr]]],
                    {"fields": ["id", "product_tmpl_id", "value_ids"]}):
                lines[l["product_tmpl_id"][0]] = (l["id"], set(l["value_ids"]))
        vcache = {}
        n = 0
        dist = Counter()
        for d in matched:
            if not d["brand"]:
                continue
            dist[d["brand"]] += 1
            if args.dry_run:
                n += 1; continue
            vid = get_or_create_value(c, vcache, attr, d["brand"], False)
            pid = byc[d["kerger"]]["id"]
            if pid not in lines:
                c._execute_kw("product.template.attribute.line", "create", [{
                    "product_tmpl_id": pid, "attribute_id": attr,
                    "value_ids": [(6, 0, [vid])]}], {})
                n += 1
            elif lines[pid][1] != {vid}:
                c._execute_kw("product.template.attribute.line", "write",
                    [[lines[pid][0]], {"value_ids": [(6, 0, [vid])]}], {})
                n += 1
        print(f"Brand: {'would set' if args.dry_run else 'set'} {n}  {dict(dist)}")

    # ---- Barcodes ----
    if args.barcodes:
        valid = [d for d in matched if re.fullmatch(r"\d{8}|\d{12,14}", d["ean"])]
        dup = {e for e, k in Counter(d["ean"] for d in valid).items() if k > 1}
        todo = [d for d in valid
                if d["ean"] not in dup and not byc[d["kerger"]]["barcode"]]
        print(f"Barcodes: {len(todo)} to set "
              f"(skipped {len(dup)} in-file dup EANs, and existing barcodes)")
        if args.apply:
            for d in todo:
                try:
                    c._execute_kw("product.template", "write",
                        [[byc[d["kerger"]]["id"]], {"barcode": d["ean"]}], {})
                except Exception as e:
                    print(f"  ! {d['kerger']} EAN {d['ean']}: {str(e)[:60]}")

    # ---- Images ----
    if args.images:
        todo = [d for d in matched
                if d["img"] and not byc[d["kerger"]]["image_1920"]]
        print(f"Images: {len(todo)} to fetch (missing image only)")
        if args.apply:
            ok = fail = 0
            for d in todo:
                try:
                    r = requests.get(d["img"], timeout=60)
                    r.raise_for_status()
                    if not r.headers.get("content-type", "").startswith("image"):
                        fail += 1; continue
                    c._execute_kw("product.template", "write",
                        [[byc[d["kerger"]]["id"]],
                         {"image_1920": base64.b64encode(r.content).decode()}], {})
                    ok += 1
                except Exception:
                    fail += 1
            print(f"  images set {ok}, failed {fail}")

    if args.dry_run:
        print("\n(dry-run — nothing written)")


if __name__ == "__main__":
    main()
