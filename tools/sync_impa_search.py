"""
Make the Odoo shop search match on product code fields (IMPA, ISSA, ...).

Odoo Online (SaaS) can't install a module to extend the product search, and the
code values live in Studio fields (e.g. x_studio_impa) that the shop search does
not index. This tool mirrors those codes into the internal `description` field —
which the shop search DOES index but which is not shown on the website — so a
customer searching a code finds the product.

Auto-detects which code fields exist. IMPA is live today; when an ISSA field is
added in Studio (label "ISSA", i.e. x_studio_issa), just re-run this — ISSA is
picked up automatically with no code change.

Idempotent: the mirrored value is wrapped in a marked <p class="o_impa_search">
paragraph, so re-running updates/removes it cleanly without touching any other
description content. Run again after catalogue imports or code edits.

Usage:
    python3 tools/sync_impa_search.py            # apply
    python3 tools/sync_impa_search.py --dry-run  # report only
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

MODEL = "product.template"
MARKER_RE = re.compile(r'<p class="o_impa_search">.*?</p>', re.DOTALL)

# Product code fields to make searchable, in display order. Each is resolved to
# an actual field name at runtime (by exact name, else by matching label), so a
# field that does not exist yet is simply skipped until it's created.
CODE_LABELS = ["IMPA", "ISSA"]


def resolve_code_fields(client):
    """Return [(label, field_name)] for code fields that exist on the model.

    When several fields share a code label (e.g. an old integer `x_studio_issa`
    and a newer text `x_studio_issa_1`), prefer a char/text field — code values
    can be multi-valued (comma-joined) and must not be truncated to an integer.
    """
    fg = client._execute_kw(MODEL, "fields_get", [], {"attributes": ["string", "type"]})
    resolved = []
    for label in CODE_LABELS:
        candidates = [k for k, v in fg.items()
                      if k == "x_studio_" + label.lower()
                      or str(v.get("string", "")).strip().lower() == label.lower()]
        if not candidates:
            continue
        text_first = sorted(candidates, key=lambda k: fg[k]["type"] not in ("char", "text"))
        resolved.append((label, text_first[0]))
    return resolved


def desired_description(current, codes):
    """Return description with the search marker set to `codes` (or removed).

    `codes` is a list of (label, value) with non-empty values.
    """
    base = MARKER_RE.sub("", current or "").strip()
    if codes:
        inner = " ".join(f"{label}: {val}" for label, val in codes)
        block = f'<p class="o_impa_search">{inner}</p>'
        return (base + block) if base else block
    return base or False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    c = OdooClient()
    c.authenticate()

    code_fields = resolve_code_fields(c)
    if not code_fields:
        sys.exit("No code fields (IMPA/ISSA) found on product.template — nothing to do.")
    print("Mirroring code field(s):", ", ".join(f"{l} ({f})" for l, f in code_fields))

    # Products that have any code set, OR already carry a stale marker.
    or_domain = [(f, "!=", False) for _, f in code_fields]
    search_domain = ["|"] * (len(or_domain) - 1) + or_domain

    ids = set(c._execute_kw(MODEL, "search", [search_domain], {}))
    ids |= set(c._execute_kw(MODEL, "search", [[["description", "ilike", "o_impa_search"]]], {}))
    ids = list(ids)

    fields = ["name", "description"] + [f for _, f in code_fields]
    recs = c._execute_kw(MODEL, "read", [ids], {"fields": fields})

    changed = 0
    for r in recs:
        codes = [(label, str(r.get(f)).strip())
                 for label, f in code_fields
                 if r.get(f) not in (False, None, "") and str(r.get(f)).strip()]
        current = r.get("description") or ""
        target = desired_description(current, codes)
        if (target or "") != (current or ""):
            changed += 1
            if args.dry_run:
                shown = ", ".join(f"{l}={v}" for l, v in codes) or "(clear)"
                print(f"  [{r['id']}] {r['name'][:38]:<38} {shown}")
            else:
                c._execute_kw(MODEL, "write", [[r["id"]], {"description": target}], {})

    verb = "would update" if args.dry_run else "updated"
    print(f"{verb} {changed} product(s); {len(recs)} inspected.")


if __name__ == "__main__":
    main()
