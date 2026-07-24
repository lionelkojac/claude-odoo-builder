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

# eCommerce lamp/lighting category tree (public categories).
# 1397 "LED lighting" added — LED products carry the same socket/voltage/etc.
# Studio specs and belong under the shop filters too.
LAMP_CATEGORIES = [1341, 1398, 1395, 1409, 1397]

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
    # Odoo returns False for an empty Studio field; str(False) -> "False",
    # so guard against falsy/placeholder values to avoid junk filter labels.
    if raw in (False, None, "", 0):
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("false", "none", "0"):
        return None
    return s


# Nominal voltage groups: explicit ranges honour the requested buckets exactly;
# anything outside them snaps to the nearest nominal ("really close = same").
_V_ANCHORS = [(6, "6V"), (12, "12V"), (26, "24–28V"), (48, "48V"),
              (60, "60V"), (110, "110V"), (220, "220V"), (380, "380V"), (440, "440V")]


def bucket_voltage(raw):
    import re as _re
    nums = [int(n) for n in _re.findall(r"\d+", str(raw))]
    if not nums:
        return None
    n = nums[0]
    if n == 6:                      return "6V"
    if n == 12:                     return "12V"
    if 24 <= n <= 28:               return "24–28V"
    if n == 48:                     return "48V"
    if n == 60:                     return "60V"
    if 100 <= n <= 130:             return "110V"
    if 200 <= n <= 245:             return "220V"
    if n == 380:                    return "380V"
    if n == 440:                    return "440V"
    # straggler → nearest nominal
    return min(_V_ANCHORS, key=lambda a: abs(a[0] - n))[1]


def kelvin(raw):
    # Color-temperature field mixes real Kelvin values ("3000", "3000 K") with
    # LED colour names ("Red"). Only numeric values are colour temperatures;
    # normalise to a single "N K" (never "K K"). Colour names -> colour().
    if raw in (False, None, "", 0):
        return None
    import re as _re
    m = _re.search(r"\d+", str(raw))
    if not m:
        return None
    return f"{m.group()} K"


_COLOURS = {"red", "green", "blue", "white", "yellow", "amber", "orange"}


def colour(raw):
    # The same field carries an LED's emitted colour for indicator lamps.
    if raw in (False, None, "", 0):
        return None
    s = str(raw).strip()
    return s.capitalize() if s.lower() in _COLOURS else None


# Each: attribute name, source Studio field, and how to derive the value label.
ATTRS = [
    {"name": "Socket", "field": "x_studio_socket2", "fn": exact},
    {"name": "Voltage", "field": "x_studio_voltage_2_v", "fn": bucket_voltage},
    {"name": "Wattage", "field": "x_studio_wattage_w", "fn": bucket_watt},
    {"name": "Color Temperature", "field": "x_studio_color_temperature", "fn": kelvin},
    {"name": "Colour", "field": "x_studio_color_temperature", "fn": colour},
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
