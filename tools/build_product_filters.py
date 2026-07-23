"""
Turn product Studio-field specs into website filter attributes.

Odoo's left-sidebar shop filters only work off product attributes, not Studio
custom fields. This tool creates "no variant" product attributes (so NO variants
are generated) from Studio fields and links each product to the matching value,
which makes the attribute appear as a sidebar filter on the shop / category page.

Config-driven: add an entry to ATTRS to support a new filter (e.g. Lumen later).
Idempotent: skips products that already have a line for the attribute, and
reuses existing attributes/values.

Usage:
    python3 tools/build_product_filters.py --only Socket        # one attribute
    python3 tools/build_product_filters.py                      # all configured
    python3 tools/build_product_filters.py --dry-run
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

# eCommerce "Lamps" category tree (public categories)
LAMP_CATEGORIES = [1341, 1398, 1395, 1409]

WATT_BUCKETS = [
    (0, 2, "≤2 W"), (2, 5, "3–5 W"), (5, 15, "6–15 W"),
    (15, 40, "16–40 W"), (40, 100, "41–100 W"), (100, 1e12, "100 W+"),
]


def bucket_watt(raw):
    try:
        w = float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if w <= 0:
        return None
    for lo, hi, lbl in WATT_BUCKETS:
        if (lo == 0 and w <= hi) or (lo < w <= hi):
            return lbl
    return None


def exact(raw):
    s = str(raw).strip()
    return s or None


def kelvin(raw):
    if raw in (False, None, "", 0):
        return None
    s = str(raw).strip()
    return f"{s} K" if s and s not in ("0", "False") else None


# Each: attribute name, source Studio field, and how to derive the value label.
ATTRS = [
    {"name": "Socket", "field": "x_studio_socket2", "fn": exact},
    {"name": "Voltage", "field": "x_studio_voltage_2_v", "fn": exact},
    {"name": "Wattage", "field": "x_studio_wattage_w", "fn": bucket_watt},
    {"name": "Color Temperature", "field": "x_studio_color_temperature", "fn": kelvin},
    # Lumen: add here once the field exists, e.g.
    # {"name": "Lumen", "field": "x_studio_lumen", "fn": bucket_lumen},
]


def get_or_create_attribute(c, name, dry):
    found = c._execute_kw("product.attribute", "search_read",
                          [[["name", "=", name]]], {"fields": ["id"]})
    if found:
        return found[0]["id"]
    if dry:
        return None
    return c._execute_kw("product.attribute", "create", [{
        "name": name,
        "create_variant": "no_variant",   # critical: no product variants
        "display_type": "radio",
        "visibility": "visible",
    }], {})


def get_or_create_value(c, cache, attr_id, label, dry):
    key = (attr_id, label)
    if key in cache:
        return cache[key]
    found = c._execute_kw("product.attribute.value", "search_read",
                          [[["attribute_id", "=", attr_id], ["name", "=", label]]],
                          {"fields": ["id"]})
    vid = found[0]["id"] if found else (None if dry else
        c._execute_kw("product.attribute.value", "create",
                      [{"attribute_id": attr_id, "name": label}], {}))
    cache[key] = vid
    return vid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Build just this attribute name")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    c = OdooClient()
    c.authenticate()

    attrs = [a for a in ATTRS if not args.only or a["name"] == args.only]
    if not attrs:
        sys.exit(f"No configured attribute named {args.only!r}")

    fields = ["id"] + [a["field"] for a in attrs]
    prods = c._execute_kw("product.template", "search_read",
                          [[["public_categ_ids", "in", LAMP_CATEGORIES]]],
                          {"fields": fields})
    print(f"{len(prods)} lamp products")

    vcache = {}
    for a in attrs:
        attr_id = get_or_create_attribute(c, a["name"], args.dry_run)
        # products already carrying this attribute (idempotency)
        existing = set()
        if attr_id:
            lines = c._execute_kw("product.template.attribute.line", "search_read",
                                  [[["attribute_id", "=", attr_id]]],
                                  {"fields": ["product_tmpl_id"]})
            existing = {l["product_tmpl_id"][0] for l in lines}

        added = 0
        for p in prods:
            if p["id"] in existing:
                continue
            label = a["fn"](p.get(a["field"]))
            if not label:
                continue
            if args.dry_run:
                added += 1
                continue
            vid = get_or_create_value(c, vcache, attr_id, label, False)
            c._execute_kw("product.template.attribute.line", "create", [{
                "product_tmpl_id": p["id"],
                "attribute_id": attr_id,
                "value_ids": [(6, 0, [vid])],
            }], {})
            added += 1
        verb = "would link" if args.dry_run else "linked"
        print(f"  {a['name']}: {verb} {added} product(s)")


if __name__ == "__main__":
    main()
