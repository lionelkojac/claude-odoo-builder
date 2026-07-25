"""
Fill missing product spec Studio-fields by parsing the product name.

Only EMPTY fields are written (existing values are never overwritten), and
values are formatted to match the existing catalogue style:
  voltage  x_studio_voltage_2_v        "230 V" | "100-240 V" | "24 AC/DC"
  wattage  x_studio_wattage_w          bare number "7" | "2.5"
  socket   x_studio_socket2            canonical base "E27" | "R7s" | "BA22d"
  colour   x_studio_color_temperature  "3000 K" or "Red" (LIGHTING cats only,
                                        so a red plug's colour is not stored as
                                        a "Color Temperature")

Usage:
  python3 tools/fill_specs_from_names.py --dry-run
  python3 tools/fill_specs_from_names.py --apply
"""

import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

# --- voltage: <num>[.dec][range]V[AC/DC], or bare <num> AC/DC ---
_NUM = r"\d{1,4}(?:[.,]\d+)?"
_VNUM = rf"(?<![\d.,])({_NUM}(?:[-/]{_NUM})?)"
_V = re.compile(_VNUM + r"\s?V\s?(AC/DC|AC-DC|DC|AC)?\b", re.I)
_VACDC = re.compile(_VNUM + r"\s?(AC/DC|AC-DC)\b", re.I)


def voltage(name):
    m = _V.search(name) or _VACDC.search(name)
    if not m:
        return None
    num = m.group(1).replace(",", ".")
    suf = (m.group(2) or "")
    acdc = "AC/DC" in suf.upper().replace("-", "/")
    return f"{num} {'AC/DC' if acdc else 'V'}"


# --- wattage: <num>W (not preceded by another digit -> no 1000->000) ---
_W = re.compile(r"(?<![\d.,])(\d{1,4}(?:[.,]\d+)?)\s?W\b")


def wattage(name):
    m = _W.search(name)
    if not m:
        return None
    return m.group(1).replace(",", ".")


# --- socket / base: canonical forms keyed by their upper-case spelling ---
_BASES = ["E10", "E11", "E12", "E14", "E26", "E27", "E39", "E40",
          "B15", "B22", "BA7s", "BA9s", "BA15d", "BA15s", "BA20d", "BA22d",
          "R7s", "G4", "G5", "G9", "G13", "G23", "GU10", "GU5.3", "GY6.35",
          "GX53", "P13.5s", "P14.5s", "P15d", "P28s", "P30d",
          "S6", "S7", "S8.5", "S14s", "S14d", "SV8.5", "SX6s",
          "W2.1x9.5d", "T5.5", "T6.8"]
_BASE_BY_UPPER = {b.upper().replace(".", r"\.").replace("(", ""): b for b in _BASES}
_BASE_RE = re.compile(r"\b(" + "|".join(
    sorted((re.escape(b.upper()) for b in _BASES), key=len, reverse=True)) + r")\b")


def socket(name):
    m = _BASE_RE.search(name.upper())
    if not m:
        return None
    up = m.group(1)
    for b in _BASES:                       # map back to canonical casing
        if b.upper() == up:
            return b
    return None


# --- colour / colour temperature (lighting products only) ---
_KELV = re.compile(r"\b([2-6][0-9]{3})\s?K\b")
_COLOURS = {"RED": "Red", "GREEN": "Green", "BLUE": "Blue", "YELLOW": "Yellow",
            "WHITE": "White", "AMBER": "Amber", "ORANGE": "Orange"}
_COL_RE = re.compile(r"\b(" + "|".join(_COLOURS) + r")\b")


def colour_temp(name):
    m = _KELV.search(name)
    if m:
        return f"{m.group(1)} K"
    m = _COL_RE.search(name.upper())
    return _COLOURS[m.group(1)] if m else None


SPECS = [
    ("x_studio_voltage_2_v", voltage, False),
    ("x_studio_wattage_w", wattage, False),
    ("x_studio_socket2", socket, False),
    ("x_studio_color_temperature", colour_temp, True),   # lighting-only
]


def lighting_category_ids(c):
    cats = c._execute_kw("product.public.category", "search_read",
        [["|", "|", "|", "|", ["name", "ilike", "lamp"], ["name", "ilike", "light"],
          ["name", "ilike", "led"], ["name", "ilike", "fluor"],
          ["name", "ilike", "pilot"]]], {"fields": ["id"]})
    return {x["id"] for x in cats}


def empty(v):
    return v in (None, False, "", 0, "0")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    c = OdooClient(); c.authenticate()
    lightcats = lighting_category_ids(c)
    fields = ["id", "name", "public_categ_ids"] + [f for f, _, _ in SPECS]
    recs = c._execute_kw("product.template", "search_read", [[]], {"fields": fields})

    plan = {f: [] for f, _, _ in SPECS}
    samples = {f: [] for f, _, _ in SPECS}
    for r in recs:
        name = r["name"] or ""
        is_light = bool(set(r["public_categ_ids"] or []) & lightcats)
        vals = {}
        for f, fn, light_only in SPECS:
            if not empty(r[f]):
                continue
            if light_only and not is_light:
                continue
            v = fn(name)
            if v:
                vals[f] = v
                plan[f].append((r["id"], v))
                if len(samples[f]) < 8:
                    samples[f].append((name[:46], v))
        if vals and args.apply:
            c._execute_kw("product.template", "write", [[r["id"]], vals], {})

    for f, _, _ in SPECS:
        print(f"{f}: {'filled' if args.apply else 'would fill'} {len(plan[f])}")
        vc = Counter(v for _, v in plan[f])
        print("   value spread:", dict(vc.most_common(10)))
        if args.dry_run:
            for nm, v in samples[f]:
                print(f"     {nm:48} -> {v}")
    print("\n" + ("applied." if args.apply else "(dry-run — nothing written)"))


if __name__ == "__main__":
    main()
