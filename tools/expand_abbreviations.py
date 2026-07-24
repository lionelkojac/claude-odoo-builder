"""
Expand approved abbreviations in active product names.

Whole-word / whole-token replacements only (regex \\b anchors + escaped
periods), applied in order. Period-abbreviations consume their trailing dot
and emit a trailing space, so glued forms like "INDIC.LAMP" become
"INDICATOR LAMP"; leftover double spaces are collapsed at the end.

Only the abbreviations the user approved are included. Deliberately NOT
touched: FR., P.O., and all standard codes (AC/DC, LED, PVC, IP, GLS, HNA,
lamp-base codes, fuse designations, LSZH/TPE, CEEFORM, IMPA/ISSA).

Usage:
  python3 tools/expand_abbreviations.py --dry-run
  python3 tools/expand_abbreviations.py --apply
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

# (pattern, replacement, label) — ORDER MATTERS (longer/compound first).
_RAW = [
    # --- joined words: just insert the missing space ---
    (r"\bCIRCUITBREAKER\b", "CIRCUIT BREAKER", "CIRCUITBREAKER"),
    (r"\bHALOGENLAMP\b", "HALOGEN LAMP", "HALOGENLAMP"),
    (r"\bSHIPSCABLE\b", "SHIPS CABLE", "SHIPSCABLE"),
    (r"\bKNIFEFUSE\b", "KNIFE FUSE", "KNIFEFUSE"),
    (r"\bCABLEGLAND\b", "CABLE GLAND", "CABLEGLAND"),
    (r"\bJUNCTIONBOX\b", "JUNCTION BOX", "JUNCTIONBOX"),
    (r"\bHANDLAMP\b", "HAND LAMP", "HANDLAMP"),
    (r"\bRINGTERMINAL\b", "RING TERMINAL", "RINGTERMINAL"),
    (r"\bFORKTERMINAL\b", "FORK TERMINAL", "FORKTERMINAL"),
    (r"\bRINGTERM\b", "RING TERMINAL", "RINGTERM"),
    (r"\bFORKTERM\b", "FORK TERMINAL", "FORKTERM"),
    # --- period abbreviations (compound/multi-letter first) ---
    (r"\bALK\.BATT\.", "ALKALINE BATTERY ", "ALK.BATT."),
    (r"\bTEL\.", "TELEPHONE ", "TEL."),
    (r"\bINDIC\.", "INDICATOR ", "INDIC."),
    (r"\bSIGN\.", "SIGNAL ", "SIGN."),
    (r"\bNAVI\.", "NAVIGATION ", "NAVI."),
    (r"\bSUBMIN\.", "SUBMINIATURE ", "SUBMIN."),
    (r"\bSEARCHL\.", "SEARCHLIGHT ", "SEARCHL."),
    (r"\bFLOODL\.", "FLOODLIGHT ", "FLOODL."),
    (r"\bINSUL\.", "INSULATING ", "INSUL."),
    (r"\bRECEPT\.", "RECEPTACLE ", "RECEPT."),
    (r"\bREFL\.", "REFLECTOR ", "REFL."),
    (r"\bTERM\.", "TERMINAL ", "TERM."),
    (r"\bCONT\.", "CONTACT ", "CONT."),
    (r"\bSPEC\.", "SPECIAL ", "SPEC."),
    (r"\bCOTT\.", "COTTON ", "COTT."),
    (r"\bFITT\.", "FITTING ", "FITT."),
    (r"\bMOUNT\.", "MOUNTING ", "MOUNT."),
    (r"\bSPER\.", "SPHERE ", "SPER."),
    (r"\bJAP\.", "JAPANESE ", "JAP."),
    (r"\bDIAM\.", "DIAMETER ", "DIAM."),
    (r"\bBATT\.", "BATTERY ", "BATT."),
    (r"\bWT\.", "WATERTIGHT ", "WT."),
    (r"\bGL\.", "GLASS ", "GL."),
    (r"\bINS\.", "INSULATED ", "INS."),
    (r"\bLAMP\.", "LAMP ", "LAMP."),
    # --- non-period truncations ---
    (r"\bINDIC\b", "INDICATOR", "INDIC"),
    (r"\bLMP\b", "LAMP", "LMP"),
    (r"\bFLUOR\b", "FLUORESCENT", "FLUOR"),
    (r"\bTHERM\b", "THERMAL", "THERM"),
    (r"\bAUX\b", "AUXILIARY", "AUX"),
    (r"\bVARN\b", "VARNISHED", "VARN"),
    (r"\bJAP\b", "JAPANESE", "JAP"),
    (r"\bTEL\b", "TELEPHONE", "TEL"),
    (r"(?<=\d)\s?MTRS\b", " METRES", "nMTRS"),
    (r"(?<=\d)\s?MTR\b", " METRE", "nMTR"),
    (r"(?<=\d)\s?MRT\b", " METRE", "nMRT"),
    (r"\bMTRS\b", "METRES", "MTRS"),
    (r"\bMTR\b", "METRE", "MTR"),
    (r"\bMRT\b", "METRE", "MRT"),
    # --- armoured (cable) ---
    (r"\bARM\.", "ARMOURED ", "ARM."),
    (r"\bARM\b", "ARMOURED", "ARM"),
    (r"\bARMORED\b", "ARMOURED", "ARMORED"),
    # --- breaker curve + spelling ---
    (r"\b([BCD])-CAR\b", r"\1-CHARACTERISTIC", "x-CAR"),
    (r"\bNICKLE\b", "NICKEL", "NICKLE"),
]
RULES = [(re.compile(p), r, lbl) for p, r, lbl in _RAW]


def transform(name):
    hits = []
    s = name
    for rx, rep, lbl in RULES:
        s, n = rx.subn(rep, s)
        if n:
            hits.append(lbl)
    s = re.sub(r"\s+([,.])", r"\1", s)      # no space before , or .
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s, hits


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--show", type=int, default=60, help="sample size to print")
    args = ap.parse_args()

    c = OdooClient(); c.authenticate()
    recs = c._execute_kw("product.template", "search_read", [[]],
                         {"fields": ["id", "name"]})
    from collections import Counter
    per_rule = Counter()
    changes = []
    for r in recs:
        new, hits = transform(r["name"] or "")
        if new != (r["name"] or ""):
            changes.append((r["id"], r["name"], new))
            per_rule.update(hits)

    print(f"active products: {len(recs)} | names changed: {len(changes)}")
    print("--- changes per rule ---")
    for lbl, n in per_rule.most_common():
        print(f"  {lbl:16} {n}")
    print(f"--- sample ({min(args.show,len(changes))} of {len(changes)}) ---")
    for _id, old, new in changes[:args.show]:
        print(f"  {old}")
        print(f"    -> {new}")

    if args.dry_run:
        # write full diff for review
        path = ".tmp/abbrev_diff.txt"
        with open(path, "w") as f:
            for _id, old, new in changes:
                f.write(f"{old}\n  -> {new}\n")
        print(f"\nfull diff written to {path}\n(dry-run — nothing saved)")
        return
    for _id, old, new in changes:
        c._execute_kw("product.template", "write", [[_id], {"name": new}], {})
    print(f"\ndone. renamed {len(changes)} products.")


if __name__ == "__main__":
    main()
