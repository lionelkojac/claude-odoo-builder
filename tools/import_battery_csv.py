"""
Import the Republic Battery supplier CSV into Odoo and set each product's
"Battery type" (attribute id 39: AGM / lead-acid).

CSV (semicolon-delimited, comma decimals), one product per row:
  0 Artikelnummer 1 Eenheid 2 Omschrijving 3 Uitgebreide Omschrijving NL
  4 Uitgebreide Omschrijving EN 5 Zoeknaam 6 Lengte 7 Breedte 8 Hoogte(mm)
  9 Gewicht(kg) 10 Verkoopprijs

Battery type = "AGM" when the name contains AGM, else "lead-acid" (covers the
maintenance-free wet-cell and lead-acid rows). Both are values of the existing
"Battery type" attribute; "lead-acid" is created if missing.

Matching is by default_code (Artikelnummer), covering active + archived records.
  - new code            -> CREATE full product (name/price/weight/dims/desc) + type
  - existing, --existing type-only (default) -> set Battery type only
  - existing, --existing full                -> also update name/price/weight/dims
An existing x_studio_description_long is never overwritten.

Usage:
  python3 tools/import_battery_csv.py --file X.csv --dry-run
  python3 tools/import_battery_csv.py --file X.csv --existing full --dry-run
  python3 tools/import_battery_csv.py --file X.csv --apply [--existing full] [--publish]
"""

import argparse
import csv
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient
from build_product_filters import get_or_create_attribute, get_or_create_value
from update_stock_status import STATUS_FIELD, ASOF_FIELD, ensure_field

BATTERY_ATTR_NAME = "Battery type"
DESC_FIELD = "x_studio_description_long"
GOODS_CATEG_ID = 1


def num(v):
    """'329,0625' -> 329.0625 ; '' -> None."""
    if v is None:
        return None
    s = str(v).strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def battery_type(name):
    return "AGM" if "AGM" in (name or "").upper() else "lead-acid"


def rows_from(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.reader(f, delimiter=";")
        header = next(rd, None)
        out = []
        for r in rd:
            if not r or not (r[0] or "").strip():
                continue
            out.append(r)
    return out


def csv_values(r, publish, stock_available, as_of):
    """Full field set for a CREATE (or a --existing full UPDATE)."""
    name = (r[2] or "").strip()
    vals = {
        "name": name,
        "default_code": (r[0] or "").strip(),
        "is_storable": True,
        "categ_id": GOODS_CATEG_ID,
    }
    price = num(r[10])
    if price is not None:
        vals["list_price"] = price
    wt = num(r[9])
    if wt is not None:
        vals["weight"] = wt
    # dimensions: CSV is in mm, the Studio fields are in cm
    for src, dst in ((6, "x_studio_length2_cm"), (7, "x_studio_width2_cm"),
                     (8, "x_studio_height2_cm")):
        mm = num(r[src])
        if mm is not None:
            vals[dst] = round(mm / 10.0, 2)
    if publish:
        vals["website_published"] = True
    if stock_available:
        # always show the "Available" badge — stocked at the supplier warehouse
        vals[STATUS_FIELD] = "available"
        vals[ASOF_FIELD] = as_of
    return vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--existing", choices=["type-only", "full"], default="type-only",
                    help="how to treat products that already exist (default type-only)")
    ap.add_argument("--publish", action="store_true",
                    help="publish newly created products to the website")
    ap.add_argument("--stock-available", action="store_true",
                    help="stamp every product with the 'Available' stock badge "
                         "(stocked at the supplier warehouse)")
    ap.add_argument("--as-of", help="badge snapshot date (YYYY-MM-DD); default today")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    dry = args.dry_run
    as_of = args.as_of or datetime.date.today().isoformat()

    c = OdooClient(); c.authenticate()
    if args.apply and args.stock_available:
        ensure_field(c, STATUS_FIELD, "Stock status")
        ensure_field(c, ASOF_FIELD, "Stock as of")
    rows = rows_from(args.file)
    codes = [(r[0] or "").strip() for r in rows]

    # existing records by code (active + archived)
    prods = c._execute_kw("product.template", "search_read",
        [[["default_code", "in", codes]]],
        {"fields": ["id", "default_code", "name", "active", "list_price",
                    "weight", DESC_FIELD],
         "context": {"active_test": False}})
    by_code = {}
    for p in prods:
        by_code.setdefault(str(p["default_code"]).strip(), []).append(p)

    # Battery type attribute + values
    attr = get_or_create_attribute(c, BATTERY_ATTR_NAME, dry)
    vcache = {}
    vids = {t: get_or_create_value(c, vcache, attr, t, dry)
            for t in ("AGM", "lead-acid")}
    # existing attribute lines for this attribute, keyed by template id
    lines = {}
    if attr:
        lines = {l["product_tmpl_id"][0]: (l["id"], set(l["value_ids"]))
                 for l in c._execute_kw("product.template.attribute.line",
                     "search_read", [[["attribute_id", "=", attr]]],
                     {"fields": ["id", "product_tmpl_id", "value_ids"]})}

    n_create = n_update = n_type = 0
    agm = lead = 0
    errors = []
    print(f"{'code':<8} {'action':<22} {'type':<9} {'name':<38} price")
    print("-" * 90)
    for r in rows:
        code = (r[0] or "").strip()
        name = (r[2] or "").strip()
        btype = battery_type(name)
        agm += btype == "AGM"; lead += btype == "lead-acid"
        recs = by_code.get(code, [])
        csv_price = num(r[10])

        if not recs:
            action = "CREATE" + ("+publish" if args.publish else "")
            n_create += 1
            n_type += 1
            print(f"{code:<8} {action:<22} {btype:<9} {name[:38]:<38} {csv_price}")
            if args.apply:
                try:
                    vals = csv_values(r, args.publish, args.stock_available, as_of)
                    vals[DESC_FIELD] = (r[4] or r[2] or "").strip()  # EN extended desc
                    new_id = c._execute_kw("product.template", "create", [vals], {})
                    # external ID is a nice-to-have for re-import matching; an old
                    # import may already own __import__.<code>, so don't let a
                    # duplicate-xmlid clash abort the product create.
                    try:
                        c._execute_kw("ir.model.data", "create", [{
                            "module": "__import__", "name": code,
                            "model": "product.template", "res_id": new_id}], {})
                    except Exception as e:
                        print(f"  ! xmlid skip {code}: {str(e)[:70]}")
                    _set_type(c, new_id, attr, vids[btype], lines)
                except Exception as e:
                    errors.append((code, str(e)[:120]))
                    print(f"  ! row {code} failed: {str(e)[:100]}")
            continue

        # existing — a code may have an active + an archived duplicate. Act on the
        # live record(s); ignore an archived one when an active sibling exists.
        active = [p for p in recs if p["active"]]
        targets = active or recs
        for p in targets:
            pid = p["id"]
            if args.existing == "full":
                action = "UPDATE-full"
                n_update += 1
                delta = ""
                if csv_price is not None and p.get("list_price") not in (None, csv_price):
                    delta = f"  [{p['list_price']}→{csv_price}]"
                print(f"{code:<8} {action:<22} {btype:<9} {name[:38]:<38} {csv_price}{delta}")
                if args.apply:
                    vals = csv_values(r, args.publish, args.stock_available, as_of)
                    if not p.get(DESC_FIELD):
                        vals[DESC_FIELD] = (r[4] or r[2] or "").strip()
                    try:
                        c._execute_kw("product.template", "write", [[pid], vals], {})
                    except Exception as e:
                        errors.append((code, str(e)[:120]))
                        print(f"  ! row {code} update failed: {str(e)[:100]}")
            else:
                action = "set-type"
                print(f"{code:<8} {action:<22} {btype:<9} "
                      f"{(p['name'] or '')[:38]:<38} (keep {p.get('list_price')})")
                if args.apply and args.stock_available:
                    c._execute_kw("product.template", "write",
                        [[pid], {STATUS_FIELD: "available", ASOF_FIELD: as_of}], {})
            n_type += 1
            if args.apply:
                _set_type(c, pid, attr, vids[btype], lines)

    print("-" * 90)
    print(f"rows: {len(rows)} | AGM: {agm}  lead-acid: {lead}")
    print(f"plan: create={n_create}  full-update={n_update}  type-set={n_type}")
    print(f"existing-mode: {args.existing} | publish-new: {args.publish} | "
          f"stock-available: {args.stock_available}")
    if args.apply:
        print(f"row errors: {len(errors)}")
        for code, msg in errors:
            print(f"  {code}: {msg}")
    if dry:
        print("\n(dry-run — nothing written. Existing descriptions are preserved.)")


def _set_type(c, pid, attr, vid, lines):
    """Ensure product template `pid` carries Battery type = vid (no variants)."""
    if pid not in lines:
        lid = c._execute_kw("product.template.attribute.line", "create", [{
            "product_tmpl_id": pid, "attribute_id": attr,
            "value_ids": [(6, 0, [vid])]}], {})
        lines[pid] = (lid, {vid})
    elif lines[pid][1] != {vid}:
        c._execute_kw("product.template.attribute.line", "write",
            [[lines[pid][0]], {"value_ids": [(6, 0, [vid])]}], {})
        lines[pid] = (lines[pid][0], {vid})


if __name__ == "__main__":
    main()
