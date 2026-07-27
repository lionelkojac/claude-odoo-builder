"""
Set a Brand on a list of products (by Kerger number).

Writes the brand two ways, matching how the catalogue already stores it:
  - x_studio_brand field  -> shown on the product page as a spec
  - "Brand" attribute (id 9, no_variant) -> the Brand shop filter (its on-page
    selector is hidden via CSS, like Voltage)

Codes come from --codes-file (whitespace/newline separated) or --codes.

Usage:
  python3 tools/set_brand.py --brand Wiska --codes-file codes.txt --dry-run
  python3 tools/set_brand.py --brand Wiska --codes-file codes.txt --apply
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient
from build_product_filters import get_or_create_attribute, get_or_create_value

BRAND_ATTR_NAME = "Brand"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--codes-file")
    ap.add_argument("--codes", help="whitespace/comma separated codes")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    raw = ""
    if args.codes_file:
        raw += open(args.codes_file).read()
    if args.codes:
        raw += " " + args.codes
    codes = [x.strip() for x in raw.replace(",", " ").split() if x.strip()]
    if not codes:
        sys.exit("ERROR: no codes given (use --codes-file or --codes)")

    c = OdooClient(); c.authenticate()
    prods = c._execute_kw("product.template", "search_read",
        [[["default_code", "in", codes]]],
        {"fields": ["id", "default_code", "x_studio_brand"],
         "context": {"active_test": False}})
    codeset = set(codes)
    present = {str(p["default_code"]).strip() for p in prods}
    # brand ALL records per code (some Kerger numbers have an active + an
    # archived duplicate — the live product must be covered, not whichever the
    # search happened to return first).
    matched = [p for p in prods if str(p["default_code"]).strip() in codeset]
    unmatched = [x for x in codes if x not in present]

    print(f"codes: {len(codes)} | matched: {len(matched)} | unmatched: {len(unmatched)}")
    if unmatched:
        print("  unmatched (skipped):", " ".join(unmatched))
    other = [(p["default_code"], p["x_studio_brand"]) for p in matched
             if p["x_studio_brand"] and p["x_studio_brand"] != args.brand]
    if other:
        print(f"  NOTE {len(other)} already have a different brand (will be overwritten):", other)

    if args.dry_run:
        print(f"\nwould set brand '{args.brand}' on {len(matched)} products.")
        print("(dry-run — nothing written)")
        return

    attr = get_or_create_attribute(c, BRAND_ATTR_NAME, False)
    lines = {l["product_tmpl_id"][0]: (l["id"], set(l["value_ids"]))
             for l in c._execute_kw("product.template.attribute.line", "search_read",
                 [[["attribute_id", "=", attr]]],
                 {"fields": ["id", "product_tmpl_id", "value_ids"]})}
    vcache = {}
    vid = get_or_create_value(c, vcache, attr, args.brand, False)

    n = 0
    for p in matched:
        pid = p["id"]
        c._execute_kw("product.template", "write",
                      [[pid], {"x_studio_brand": args.brand}], {})
        if pid not in lines:
            c._execute_kw("product.template.attribute.line", "create", [{
                "product_tmpl_id": pid, "attribute_id": attr,
                "value_ids": [(6, 0, [vid])]}], {})
        elif lines[pid][1] != {vid}:
            c._execute_kw("product.template.attribute.line", "write",
                [[lines[pid][0]], {"value_ids": [(6, 0, [vid])]}], {})
        n += 1
    print(f"\nset brand '{args.brand}' on {n} products (field + Brand filter).")


if __name__ == "__main__":
    main()
