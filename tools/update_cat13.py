"""
Update category-13 (LED) products from ODOO_importCat_13.xlsx.

Scope (per user "also update other fields", with the curated data protected):
  - UPDATE : list_price (col8), weight (col12), is_storable, and image_1920
  - UNARCHIVE an archived row when it has NO active twin and disc != yes
  - PRESERVE: name, x_studio_description_long, description_sale, specs,
    category, barcode, stock  (left exactly as curated / to other workflows)

Targeting is by default_code -> the ACTIVE record (post-dedup default_code is
unique among active products). If no active record exists we fall back to the
archived one and unarchive it — this avoids re-creating a de-duped duplicate
(e.g. 130776 has an active twin, so its archived copy is left alone).

Images: matched by the product code embedded in the filename (the file's image
column is shifted one row), never by row position.

Usage:
  python3 tools/update_cat13.py --dry-run
  python3 tools/update_cat13.py --apply
"""

import argparse
import base64
import os
import sys

import openpyxl
import requests

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient
from update_cat13_images import codes_from_url  # filename -> [code,...]

XLSX = ".tmp/import_cat13.xlsx"


def numf(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def fetch_b64(url):
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    if not r.headers.get("content-type", "").startswith("image"):
        return None
    return base64.b64encode(r.content).decode()


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    rows = [r for r in list(wb["Blad1"].iter_rows(values_only=True))[1:]
            if r[0] not in (None, "")]
    codes = [str(r[0]) for r in rows]
    img_by_code = {}
    for r in rows:
        for cd in codes_from_url(r[18]):
            img_by_code.setdefault(cd, r[18])

    c = OdooClient(); c.authenticate()
    recs = c._execute_kw("product.template", "search_read",
        [[["default_code", "in", codes + list(img_by_code)]]],
        {"fields": ["id", "default_code", "active"],
         "context": {"active_test": False}})
    active_of, archived_of = {}, {}
    for p in recs:
        dc = (p["default_code"] or "").strip()
        (active_of if p["active"] else archived_of)[dc] = p["id"]

    def target(code, disc_yes):
        """Return (res_id, will_unarchive) or (None, False)."""
        if code in active_of:
            return active_of[code], False
        if code in archived_of and not disc_yes:
            return archived_of[code], True
        return None, False

    upd = unarch = skip = 0
    field_plan = []          # (res_id, vals, unarchive)
    for r in rows:
        code = str(r[0])
        disc_yes = str(r[17]).strip().lower() == "yes" if r[17] else False
        rid, will_un = target(code, disc_yes)
        if rid is None:
            skip += 1
            continue
        vals = {"is_storable": True}
        if numf(r[8]) is not None:
            vals["list_price"] = numf(r[8])
        if numf(r[12]) not in (None, 0):
            vals["weight"] = numf(r[12])
        if will_un:
            vals["active"] = True
            unarch += 1
        upd += 1
        field_plan.append((code, rid, vals, will_un))

    # image plan (target active-preferred, incl. just-unarchived)
    img_plan = []
    for code, url in img_by_code.items():
        rid, will_un = target(code, False)
        if rid is not None:
            img_plan.append((code, rid, url))

    print(f"rows={len(rows)} | field updates={upd} (unarchive={unarch}) | "
          f"skipped={skip} | images={len(img_plan)}")

    if args.dry_run:
        for code, rid, vals, un in field_plan[:15]:
            tag = " UNARCHIVE" if un else ""
            print(f"  {code} id={rid}{tag}  price={vals.get('list_price','-')} "
                  f"weight={vals.get('weight','-')}")
        print(f"  ...({len(field_plan)} field updates, {len(img_plan)} images)")
        print("\n(dry-run — nothing written, no images fetched)")
        return

    for code, rid, vals, un in field_plan:
        c._execute_kw("product.template", "write", [[rid], vals], {})
    print(f"field updates written: {len(field_plan)} (unarchived {unarch})")

    ok = fail = 0
    for code, rid, url in img_plan:
        try:
            b64 = fetch_b64(url)
            if not b64:
                fail += 1; continue
            c._execute_kw("product.template", "write",
                          [[rid], {"image_1920": b64}], {})
            ok += 1
            if ok % 20 == 0:
                print(f"  ...images {ok}/{len(img_plan)}")
        except Exception as e:
            fail += 1; print(f"  ! image {code}: {str(e)[:80]}")
    print(f"images updated={ok} failed={fail}")


if __name__ == "__main__":
    main()
