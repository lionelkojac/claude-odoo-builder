"""
Build shop filters for non-lamp categories by parsing the product NAME.

Unlike build_product_filters.py (which reads lamp Studio fields), these
categories carry their specs in the name, so each attribute has a parser
fn(name) -> value | [values] | None. Reuses the attribute/value helpers and
the same reconcile behaviour (create or update lines; multi-value supported).

Attributes are no_variant (never create variants). Single-valued attributes
stay visible (they read as a spec line); the shared "Voltage" attribute is
reused where relevant and is already hidden on product pages via CSS.

Usage:
  python3 tools/build_category_filters.py --category Batteries --dry-run
  python3 tools/build_category_filters.py --category Batteries --apply
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient
from build_product_filters import get_or_create_attribute, get_or_create_value


# ---------------- battery parsers ----------------
def _voltage(name):
    # matches 230V, 24V, 1,5V and also 24VAC / 24VDC / 24VAC/DC
    m = re.search(r"(\d+(?:[.,]\d+)?)\s?V(?:AC|DC|AC/DC|AC-DC)?\b", name)
    return f"{m.group(1).replace(',', '.')}V" if m else None


def battery_chemistry(name):
    u = name.upper()
    if "ALKALINE" in u or "ALK.BATT" in u or "ALK BATT" in u:
        return "Alkaline"
    if "NIMH" in u:
        return "NiMH"
    if "NICAD" in u or "NICD" in u or "NI-CD" in u:
        return "NiCd"
    if "LITHIUM" in u or re.search(r"\bCR ?\d", u):
        return "Lithium"
    if re.search(r"\bSR ?\d", u) or "BUTTON CELL" in u or "SILVER" in u:
        return "Silver oxide"
    if "MERC" in u:
        return "Mercury"
    if "DRY BATTERY" in u or re.search(r"\b\d?R ?\d", u):
        return "Zinc-carbon"
    return None


def battery_size(name):
    u = name.upper()
    if re.search(r"\bAAA\b|\bLR ?03\b|\bHR ?03\b|\bMN2400\b|\bR ?03\b", u):
        return "AAA"
    if re.search(r"\bAA\b|\bLR ?6\b|\bHR ?6\b|\bMN1500\b|\bR ?6\b", u):
        return "AA"
    if re.search(r"\bSIZE C\b|\bLR ?14\b|\bHR ?14\b|\bMN1400\b|\bR ?14\b", u):
        return "C"
    if re.search(r"\bSIZE D\b|\bLR ?20\b|\bHR ?20\b|\bMN1300\b|\bR ?20\b|\bHR20\b", u):
        return "D"
    if re.search(r"\b9V\b|6LR61|MN1604|HR9V|\bPP3\b|6F22", u):
        return "9V"
    if re.search(r"\bCR ?\d{3,}|\bSR ?\d|BUTTON CELL|COIN|\bLR ?\d{2}\b|A76|\bPX\d", u):
        return "Coin / button cell"
    if re.search(r"\b4R25\b|SPRING", u):
        return "Lantern (4R25)"
    return None


# ---------- shared electrical parsers (breakers / fuses / relays) ----------
def poles(name):
    m = re.search(r"\b([1-4])\s*-?\s*(?:P\b|P\.|POL|POLE)", name.upper())
    return f"{m.group(1)}-pole" if m else None


def rated_current(name):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s?(?:A\b|AMP|AMPS)", name, re.I)
    return f"{m.group(1).replace(',', '.')} A" if m else None


def breaking_ka(name):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s?kA\b", name, re.I)
    return f"{m.group(1).replace(',', '.')} kA" if m else None


def cb_characteristic(name):
    u = name.upper()
    if "B-CHAR" in u or "B-CAR" in u:
        return "B curve"
    if "C-CHAR" in u or "C-CAR" in u:
        return "C curve"
    if "D-CHAR" in u or "D-CAR" in u:
        return "D curve"
    return None


def fuse_type(name):
    u = name.upper()
    if "GLASS" in u:
        return "Glass"
    if "CERAMIC" in u or "CER." in u:
        return "Ceramic"
    if "KNIFE" in u or "BLADE" in u or re.search(r"\bNH", u):
        return "DIN knife (NH)"
    if "DZD" in u:
        return "Diazed (D-type)"
    if "NDZ" in u:
        return "Neozed (D-type)"
    if "FR.FUSE" in u or "FR FUSE" in u:
        return "Cylindrical cartridge"
    return None


def fuse_size(name):
    m = re.search(r"(\d+)\s?X\s?(\d+(?:[.,]\d+)?)\s?MM", name, re.I)
    if m:
        return f"{m.group(1)}x{m.group(2).replace(',', '.')} mm"
    u = name.upper()
    for tok, lbl in [("NHOO", "NH00"), ("NHOI", "NH1"), ("NHII", "NH2"),
                     ("NHO", "NH0")]:
        if tok in u:
            return lbl
    return None


def fuse_speed(name):
    u = name.upper()
    if re.search(r"\bAM\b", u):
        return "aM (motor)"
    if "GG" in u or re.search(r"\bG1\b|\bGL\b", u):
        return "gG (general)"
    if "DELAYED" in u or "SLOW" in u:
        return "Time-delay (T)"
    if "QUICK" in u or "FAST" in u:
        return "Quick-acting (F)"
    return None


def rc_type(name):
    u = name.upper()
    if "CONTACTOR" in u:
        return "Contactor"
    if "CONTACT BLOCK" in u or "CONTACTBLOCK" in u or "CONT BLCK" in u or "CONT BLOCK" in u:
        return "Contact block"
    if "OVERLOAD" in u:
        return "Overload relay"
    if "RELAY" in u:
        return "Relay"
    if "COIL" in u:
        return "Coil"
    if u.startswith("CONTACT "):
        return "Contactor"
    return None


def contact_config(name):
    m = re.search(r"(\d?N[OC](?:\s?[+/]\s?\d?N[OC])*)", name.upper())
    if not m:
        return None
    return re.sub(r"\s", "", m.group(1)).replace("/", "+")


def capacitance_uf(name):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s?[UµM]F\b", name, re.I)
    return f"{m.group(1).replace(',', '.')} µF" if m else None


def _lead_num(label):
    m = re.match(r"\s*(\d+(?:\.\d+)?)", label)
    return float(m.group(1)) if m else 1e9


# ---------------- category configs ----------------
# fn returns a label (str), list of labels, or None. Voltage reuses the shared
# "Voltage" attribute so there is one voltage filter concept site-wide.
CATEGORIES = {
    "Batteries": {
        "category_ids": [1400, 1344],
        "attributes": [
            {"name": "Voltage", "fn": _voltage},
            {"name": "Chemistry", "fn": battery_chemistry},
            {"name": "Battery size", "fn": battery_size},
        ],
    },
    "Capacitors": {
        "category_ids": [1353],
        "attributes": [
            {"name": "Capacitance", "fn": capacitance_uf, "numeric": True},
        ],
    },
    "Circuit breakers": {
        "category_ids": [1362],
        "attributes": [
            {"name": "Poles", "fn": poles},
            {"name": "Rated current", "fn": rated_current, "numeric": True},
            {"name": "Tripping characteristic", "fn": cb_characteristic},
            {"name": "Breaking capacity", "fn": breaking_ka, "numeric": True},
        ],
    },
    "Fuses": {
        "category_ids": [1361],
        "attributes": [
            {"name": "Rated current", "fn": rated_current, "numeric": True},
            {"name": "Fuse type", "fn": fuse_type},
            {"name": "Fuse size", "fn": fuse_size},
            {"name": "Fuse speed/class", "fn": fuse_speed},
        ],
    },
    "Relays and contactors": {
        "category_ids": [1350],
        "attributes": [
            {"name": "Type", "fn": rc_type},
            {"name": "Rated current", "fn": rated_current, "numeric": True},
            {"name": "Coil voltage", "fn": _voltage, "numeric": True},
            {"name": "Contact configuration", "fn": contact_config},
        ],
    },
}


def run(c, cfg, dry, show):
    prods = c._execute_kw("product.template", "search_read",
        [[["public_categ_ids", "in", cfg["category_ids"]],
          ["website_published", "=", True]]], {"fields": ["id", "name"]})
    print(f"{len(prods)} published products")
    vcache = {}
    for a in cfg["attributes"]:
        attr_id = get_or_create_attribute(c, a["name"], dry, a.get("hidden", False))
        line_of = {}
        if attr_id:
            for l in c._execute_kw("product.template.attribute.line", "search_read",
                    [[["attribute_id", "=", attr_id]]],
                    {"fields": ["id", "product_tmpl_id", "value_ids"]}):
                line_of[l["product_tmpl_id"][0]] = (l["id"], set(l["value_ids"]))
        created = updated = matched = 0
        samples = []
        for p in prods:
            labels = a["fn"](p["name"] or "")
            if not labels:
                continue
            if isinstance(labels, str):
                labels = [labels]
            matched += 1
            if len(samples) < show:
                samples.append((p["name"][:44], labels))
            if dry:
                continue
            want = {get_or_create_value(c, vcache, attr_id, x, False) for x in labels}
            if p["id"] not in line_of:
                c._execute_kw("product.template.attribute.line", "create", [{
                    "product_tmpl_id": p["id"], "attribute_id": attr_id,
                    "value_ids": [(6, 0, list(want))]}], {})
                created += 1
            elif line_of[p["id"]][1] != want:
                c._execute_kw("product.template.attribute.line", "write",
                    [[line_of[p["id"]][0]], {"value_ids": [(6, 0, list(want))]}], {})
                updated += 1
        if dry:
            print(f"  {a['name']}: {matched}/{len(prods)} parse a value")
            for nm, lbls in samples:
                print(f"     {nm:46} -> {lbls}")
        else:
            if a.get("numeric") and attr_id:
                vals = c._execute_kw("product.attribute.value", "search_read",
                    [[["attribute_id", "=", attr_id]]], {"fields": ["id", "name"]})
                for seq, v in enumerate(sorted(vals, key=lambda x: _lead_num(x["name"]))):
                    c._execute_kw("product.attribute.value", "write",
                                  [[v["id"]], {"sequence": seq}], {})
            print(f"  {a['name']}: matched {matched} (created {created}, "
                  f"updated {updated})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", required=True, choices=list(CATEGORIES))
    ap.add_argument("--show", type=int, default=50)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    c = OdooClient(); c.authenticate()
    run(c, CATEGORIES[args.category], args.dry_run, args.show)


if __name__ == "__main__":
    main()
