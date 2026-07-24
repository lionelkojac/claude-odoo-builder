"""
Import / unarchive products from an Odoo-export xlsx, matched by External ID.

Per row (sheet 'Blad1'):
  - match col 0 (External ID) via ir.model.data (module __import__, name=<extid>)
  - matched + archived  -> unarchive (active=True) + update fields
  - matched + active    -> update fields
  - no match            -> create product + register the External ID
Skips rows where Discontinued == 'yes', and de-dupes repeated External IDs.

Column map (positional — headers are ambiguous/duplicated):
  0 External ID | 1 default_code | 2 Name | 8 list_price | 12 weight
  6 eCommerce category ext id | 7 internal category | 15 barcode (skip if empty)
  17 Discontinued | 19 Publish
NOT imported (by decision): 13 Search Key (unclear), 16 stock (left to Wholesale
sync), 9-11 dimensions (all zero), 18 Image (host blocked), 20-24 lamp specs
(bogus on these products). Description field is set to the Name per instruction.

Usage:
  python3 tools/import_products_xlsx.py --file .tmp/import_cat.xlsx --dry-run
  python3 tools/import_products_xlsx.py --file .tmp/import_cat.xlsx --apply
"""

import argparse
import base64
import os
import sys

import openpyxl
import requests

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

GOODS_CATEG_ID = 1          # product.category "Goods"
DESC_FIELD = "x_studio_description_long"


def clean(v):
    return None if v is None else str(v).strip()


def fetch_image_b64(url):
    """Download an image URL and return base64 (for image_1920), or None."""
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        if not r.headers.get("content-type", "").startswith("image"):
            return None
        return base64.b64encode(r.content).decode()
    except Exception:
        return None


def rows_from(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Blad1"]
    out = []
    for r in list(ws.iter_rows(values_only=True))[1:]:
        if r[0] in (None, ""):
            continue
        out.append(r)
    return out


def build_values(r, public_categ_id):
    """Identity/price/category fields. Description handled separately so we can
    preserve an existing one; specs are never written (kept as-is)."""
    vals = {
        "name": clean(r[2]),
        "default_code": clean(r[1]),
        "is_storable": True,
        "website_published": True,            # publish always true
        "categ_id": GOODS_CATEG_ID,
    }
    if r[8] not in (None, ""):
        vals["list_price"] = float(r[8])
    if r[12] not in (None, "", 0):
        vals["weight"] = float(r[12])
    bc = clean(r[15])
    if bc and bc not in ("0", "None"):        # skip barcode if none
        vals["barcode"] = bc
    if public_categ_id:
        vals["public_categ_ids"] = [(6, 0, [public_categ_id])]
    return vals


def category_cache(c, rows):
    """Resolve each distinct col6 category ext-id to a public category res_id."""
    ext = sorted({clean(r[6]) for r in rows if r[6] is not None})
    imd = c._execute_kw("ir.model.data", "search_read",
        [[["model", "=", "product.public.category"], ["name", "in", ext]]],
        {"fields": ["name", "res_id"]})
    return {d["name"]: d["res_id"] for d in imd}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    c = OdooClient(); c.authenticate()

    rows = rows_from(args.file)
    catcache = category_cache(c, rows)
    # skip discontinued, de-dupe by external id (keep first)
    seen, plan = set(), []
    skipped_disc = dups = 0
    for r in rows:
        if clean(r[17]) and clean(r[17]).lower() == "yes":
            skipped_disc += 1
            continue
        ext = clean(r[0])
        if ext in seen:
            dups += 1
            continue
        seen.add(ext)
        plan.append(r)

    ext_ids = [clean(r[0]) for r in plan]
    imd = c._execute_kw("ir.model.data", "search_read",
        [[["model", "=", "product.template"], ["name", "in", ext_ids]]],
        {"fields": ["name", "res_id"]})
    ext_to_res = {d["name"]: d["res_id"] for d in imd}
    # existing state + whether they already have a description (preserve it)
    existing = c._execute_kw("product.template", "search_read",
        [["&", ["id", "in", list(ext_to_res.values())],
          "|", ["active", "=", True], ["active", "=", False]]],
        {"fields": ["id", "active", DESC_FIELD]})
    states = {p["id"]: p["active"] for p in existing}
    has_desc = {p["id"]: bool(p[DESC_FIELD]) for p in existing}

    n_unarch = n_update = n_create = 0
    img_ok = img_fail = 0
    for r in plan:
        ext = clean(r[0])
        vals = build_values(r, catcache.get(clean(r[6])))
        res = ext_to_res.get(ext)
        # description = name ONLY when the product has none (never clobber)
        if not (res and res in has_desc and has_desc[res]):
            vals[DESC_FIELD] = clean(r[2])
        if args.apply and clean(r[18]):
            img = fetch_image_b64(clean(r[18]))
            if img:
                vals["image_1920"] = img
                img_ok += 1
            else:
                img_fail += 1
                print(f"  ! image fetch failed for {ext}")
        if res and res in states:
            archived = not states[res]
            action = "UNARCHIVE+update" if archived else "update"
            if archived:
                n_unarch += 1
            else:
                n_update += 1
            if args.apply:
                if archived:
                    vals["active"] = True
                c._execute_kw("product.template", "write", [[res], vals], {})
        else:
            action = "CREATE"
            n_create += 1
            if args.apply:
                new_id = c._execute_kw("product.template", "create", [vals], {})
                c._execute_kw("ir.model.data", "create", [{
                    "module": "__import__", "name": ext,
                    "model": "product.template", "res_id": new_id}], {})
        if args.dry_run:
            has_img = "img" if clean(r[18]) else "—"
            print(f"  {ext:<8} {action:<16} {vals['name'][:34]:<34} "
                  f"price={vals.get('list_price','-')!s:<7} {has_img}")

    print(f"\nrows: {len(rows)} | discontinued skipped: {skipped_disc} | "
          f"duplicate rows skipped: {dups}")
    print(f"plan: unarchive={n_unarch}  update={n_update}  create={n_create}  "
          f"({len(catcache)} categories resolved)")
    if args.apply:
        print(f"images: {img_ok} attached, {img_fail} failed")
    if args.dry_run:
        print("\n(dry-run — nothing written. Images NOT included; spec/search/stock "
              "columns intentionally skipped.)")


if __name__ == "__main__":
    main()
