"""
IP rating: a spec field shown on the product page + a multi-value shop filter.

- x_studio_ip_rating (char): the display value, e.g. "IP66/IP67". Populated
  from the product name where present, plus explicit datasheet/catalogue
  values (Siemens limit switches, Schneider XB4). Shown as a "IP rating" spec.
- "IP rating" attribute: one value per individual level (IP66/IP67 -> IP66 and
  IP67), so a combined rating is filterable under each. The on-page selector is
  hidden via CSS (like Voltage/Brand); the field carries the display.

Usage:
  python3 tools/build_ip_rating.py --dry-run
  python3 tools/build_ip_rating.py --apply
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient
from build_product_filters import get_or_create_attribute, get_or_create_value

PT_MODEL_ID = 387
FIELD = "x_studio_ip_rating"

# explicit values from datasheets / the Siemens catalogue (names lack IP)
OVERRIDE = {
    "462505": "IP66/IP67", "462507": "IP66/IP67", "462510": "IP66/IP67",
    "462516": "IP66/IP67", "462525": "IP66/IP67", "462536": "IP66/IP67",
    "383780": "IP66/IP69K", "383781": "IP66/IP69K",
    "383782": "IP66/IP67/IP69/IP69K", "383783": "IP67/IP69K",
    "383784": "IP66/IP67/IP69/IP69K",
}

# \b so it doesn't match inside GRIP/CLIP/STRIP; K? is the only valid suffix
# (IP69K). First digit of an IP code is 0-6, second 0-9.
_IP = re.compile(r"\bIP\s?[0-6]\d[Kk]?(?:\s?/\s?(?:IP)?[0-6]\d[Kk]?)*", re.I)


def ip_display(name):
    m = _IP.search(name or "")
    if not m:
        return None
    parts = re.sub(r"\s", "", m.group(0).upper()).split("/")
    return "/".join(p if p.startswith("IP") else "IP" + p for p in parts)


def ip_levels(disp):
    return [p for p in disp.split("/")] if disp else []


def ensure_field(c):
    """Create the x_studio_ip_rating char field if missing (data only). IP is
    DISPLAYED on the page via the 'IP rating' attribute in the Specifications
    table, so no website.sale.extra.field is created here."""
    f = c._execute_kw("ir.model.fields", "search_read",
        [[["model", "=", "product.template"], ["name", "=", FIELD]]],
        {"fields": ["id"]})
    if f:
        return f[0]["id"]
    return c._execute_kw("ir.model.fields", "create", [{
        "name": FIELD, "field_description": "IP rating", "model_id": PT_MODEL_ID,
        "model": "product.template", "ttype": "char", "state": "manual"}], {})


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    c = OdooClient(); c.authenticate()
    if args.apply:
        ensure_field(c)

    recs = c._execute_kw("product.template", "search_read", [[]],
                         {"fields": ["id", "default_code", "name"]})
    # resolve each product's IP display value
    plan = []
    for r in recs:
        disp = OVERRIDE.get(str(r["default_code"]).strip()) or ip_display(r["name"])
        if disp:
            plan.append((r["id"], disp))
    print(f"{len(plan)} products with an IP rating")

    if args.dry_run:
        from collections import Counter
        cc = Counter(d for _, d in plan)
        print("values:", dict(cc.most_common(20)))
        print("(dry-run — nothing written)")
        return

    # 1) write the display field
    for pid, disp in plan:
        c._execute_kw("product.template", "write", [[pid], {FIELD: disp}], {})

    # 2) build the multi-value IP filter attribute (selector hidden via CSS)
    attr = get_or_create_attribute(c, "IP rating", False)
    lines = {l["product_tmpl_id"][0]: (l["id"], set(l["value_ids"]))
             for l in c._execute_kw("product.template.attribute.line", "search_read",
                 [[["attribute_id", "=", attr]]],
                 {"fields": ["id", "product_tmpl_id", "value_ids"]})}
    vcache = {}
    created = updated = 0
    for pid, disp in plan:
        want = {get_or_create_value(c, vcache, attr, lvl, False)
                for lvl in ip_levels(disp)}
        if pid not in lines:
            c._execute_kw("product.template.attribute.line", "create", [{
                "product_tmpl_id": pid, "attribute_id": attr,
                "value_ids": [(6, 0, list(want))]}], {})
            created += 1
        elif lines[pid][1] != want:
            c._execute_kw("product.template.attribute.line", "write",
                [[lines[pid][0]], {"value_ids": [(6, 0, list(want))]}], {})
            updated += 1
    print(f"IP field set on {len(plan)}; filter lines created {created}, "
          f"updated {updated}. Attribute id = {attr} (hide its on-page selector "
          "in the CSS block).")


if __name__ == "__main__":
    main()
