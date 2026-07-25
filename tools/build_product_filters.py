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


# Nominal voltage buckets, each with the numeric band it covers. A product
# voltage RANGE is tagged with every bucket its band overlaps, so a "12-30 V"
# item shows up under 12V and 24–28V; a universal "100-240 V" under 110V and
# 220V. A single value falls in its band, else snaps to the nearest nominal.
_V_BUCKETS = [("6V", 6, 6), ("12V", 12, 12), ("24–28V", 24, 28), ("48V", 48, 48),
              ("60V", 60, 60), ("110V", 100, 130), ("220V", 200, 245),
              ("380V", 380, 380), ("440V", 440, 440)]


def bucket_voltage(raw):
    """Return a LIST of nominal voltage buckets for this value (or None)."""
    import re as _re
    nums = [int(n) for n in _re.findall(r"\d+", str(raw))]
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    if lo == hi:                                   # single value
        inside = [lbl for lbl, blo, bhi in _V_BUCKETS if blo <= lo <= bhi]
        if inside:
            return inside
        mid = lambda b: (b[1] + b[2]) / 2
        return [min(_V_BUCKETS, key=lambda b: abs(mid(b) - lo))[0]]
    # range: every bucket whose band overlaps [lo, hi]
    out = [lbl for lbl, blo, bhi in _V_BUCKETS if blo <= hi and bhi >= lo]
    if not out:                                    # range between bands
        mid = (lo + hi) / 2
        out = [min(_V_BUCKETS, key=lambda b: abs((b[1] + b[2]) / 2 - mid))[0]]
    return out


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


# Each: attribute name, source Studio field, how to derive the value label.
# NOTE Voltage stays VISIBLE (needed for the shop-listing filter). A range
# gets several buckets (12V + 24–28V); on the PRODUCT PAGE those would render
# as multiple radios (looking like two "types"), so the product-page selector
# is hidden with CSS (block 'hide_voltage_selector', .tmp/hide_voltage.css) —
# which targets li[name="variant_attribute"], not the listing filter markup.
# (visibility='hidden' is NOT usable: it removes the sidebar filter entirely.)
ATTRS = [
    {"name": "Socket", "field": "x_studio_socket2", "fn": exact},
    {"name": "Voltage", "field": "x_studio_voltage_2_v", "fn": bucket_voltage},
    {"name": "Wattage", "field": "x_studio_wattage_w", "fn": bucket_watt},
    {"name": "Color Temperature", "field": "x_studio_color_temperature", "fn": kelvin},
    {"name": "Colour", "field": "x_studio_color_temperature", "fn": colour},
    # Lumen: add here once the field exists, e.g.
    # {"name": "Lumen", "field": "x_studio_lumen", "fn": bucket_lumen},
]


def get_or_create_attribute(c, name, dry, hidden=False):
    vis = "hidden" if hidden else "visible"
    found = c._execute_kw("product.attribute", "search_read",
                          [[["name", "=", name]]], {"fields": ["id", "visibility"]})
    if found:
        if not dry and found[0]["visibility"] != vis:
            c._execute_kw("product.attribute", "write",
                          [[found[0]["id"]], {"visibility": vis}], {})
        return found[0]["id"]
    if dry:
        return None
    return c._execute_kw("product.attribute", "create", [{
        "name": name,
        "create_variant": "no_variant",   # critical: no product variants
        "visibility": vis,
        "display_type": "radio",
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
        attr_id = get_or_create_attribute(c, a["name"], args.dry_run,
                                          a.get("hidden", False))
        # existing lines with their current value set (to reconcile, not skip —
        # so a re-run can add newly-parsed specs and extra voltage buckets)
        line_of = {}
        if attr_id:
            lines = c._execute_kw("product.template.attribute.line", "search_read",
                                  [[["attribute_id", "=", attr_id]]],
                                  {"fields": ["id", "product_tmpl_id", "value_ids"]})
            line_of = {l["product_tmpl_id"][0]: (l["id"], set(l["value_ids"]))
                       for l in lines}

        created = updated = 0
        for p in prods:
            labels = a["fn"](p.get(a["field"]))
            if not labels:
                continue
            if isinstance(labels, str):
                labels = [labels]
            if args.dry_run:
                if p["id"] not in line_of:
                    created += 1
                continue
            want = {get_or_create_value(c, vcache, attr_id, lbl, False)
                    for lbl in labels}
            if p["id"] not in line_of:
                c._execute_kw("product.template.attribute.line", "create", [{
                    "product_tmpl_id": p["id"], "attribute_id": attr_id,
                    "value_ids": [(6, 0, list(want))]}], {})
                created += 1
            else:
                line_id, have = line_of[p["id"]]
                if want != have:
                    c._execute_kw("product.template.attribute.line", "write",
                                  [[line_id], {"value_ids": [(6, 0, list(want))]}], {})
                    updated += 1
        verb = "would create" if args.dry_run else "created"
        print(f"  {a['name']}: {verb} {created}" +
              ("" if args.dry_run else f", updated {updated}") + " line(s)")


if __name__ == "__main__":
    main()
