"""
Correct category-13 (LED) product images from ODOO_importCat_13.xlsx.

The file's image COLUMN is shifted down one row (every row carries the
previous row's image), so row position is unreliable. Instead we match each
image to the product code embedded in its filename:

    .../Photos-catalogue/130010.png        -> product 130010
    .../Photos-catalogue/k136300.JPG        -> product 136300
    .../Photos-catalogue/K136366+-K136368.JPG -> products 136366 AND 136368

Only image_1920 is written. Names, descriptions, price, category are left
untouched (they were curated separately) — this fixes images only.

Match: filename code -> External ID (ir.model.data, product.template) -> the
active kept record.

Usage:
  python3 tools/update_cat13_images.py --dry-run
  python3 tools/update_cat13_images.py --apply
"""

import argparse
import base64
import os
import re
import sys

import openpyxl
import requests

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

XLSX = ".tmp/import_cat13.xlsx"
# <code>[_n] . png|jpg, optional k/K prefix; combined "K136366+-K136368"
FN = re.compile(r"2F[kK]?(\d+)(?:_\d+)?(?:\+-[kK]?(\d+))?\.(?:png|jpe?g)", re.I)


def codes_from_url(u):
    if not u:
        return []
    m = FN.search(u)
    if not m:
        return []
    return [g for g in m.groups() if g]


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
    ws = wb["Blad1"]
    rows = [r for r in list(ws.iter_rows(values_only=True))[1:]
            if r[0] not in (None, "")]

    # code -> image url (from the filename, ignoring row position)
    code_url = {}
    for r in rows:
        for code in codes_from_url(r[18]):
            code_url.setdefault(code, r[18])
    print(f"{len(rows)} rows | {len(code_url)} product images found by filename")

    c = OdooClient(); c.authenticate()
    # resolve External IDs -> res_id, and active state
    imd = c._execute_kw("ir.model.data", "search_read",
        [[["model", "=", "product.template"], ["name", "in", list(code_url)]]],
        {"fields": ["name", "res_id"]})
    ext_to_res = {d["name"]: d["res_id"] for d in imd}
    res_ids = list(ext_to_res.values())
    active = c._execute_kw("product.template", "search_read",
        [[["id", "in", res_ids]]], {"fields": ["id", "name"]})
    act = {p["id"]: p["name"] for p in active}

    unresolved = [k for k in code_url if k not in ext_to_res]
    inactive = [k for k in code_url if ext_to_res.get(k) not in act]
    todo = [k for k in code_url if ext_to_res.get(k) in act]
    print(f"resolved to active products: {len(todo)} | "
          f"unresolved ext id: {len(unresolved)} | archived: "
          f"{len(inactive)-len(unresolved)}")
    if unresolved:
        print("  unresolved codes:", unresolved)

    if args.dry_run:
        for code in sorted(todo)[:20]:
            rid = ext_to_res[code]
            print(f"  {code} -> id {rid}  {act[rid][:44]}")
        print(f"  ... ({len(todo)} products would get their image)")
        print("\n(dry-run — no images fetched or written)")
        return

    ok = fail = 0
    for code in todo:
        rid = ext_to_res[code]
        try:
            b64 = fetch_b64(code_url[code])
            if not b64:
                fail += 1; print(f"  ! {code}: not an image"); continue
            c._execute_kw("product.template", "write",
                          [[rid], {"image_1920": b64}], {})
            ok += 1
            if ok % 20 == 0:
                print(f"  ...{ok}/{len(todo)}")
        except Exception as e:
            fail += 1
            print(f"  ! {code}: {str(e)[:90]}")
    print(f"\ndone. images updated={ok} failed={fail}")


if __name__ == "__main__":
    main()
