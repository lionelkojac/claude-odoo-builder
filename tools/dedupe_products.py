"""
De-duplicate product.template records that share an internal reference.

The catalogue was imported twice (Oct-2025 and Feb-2026), leaving ~437
internal references with two active records. The canonical record carries a
registered External ID (ir.model.data / module __import__); the redundant
copy usually has none.

Rule (per user): in each group of active products sharing a default_code,
ARCHIVE the record(s) that have NO External ID, keeping the External-ID one.

Safety guards:
  - only archive a no-ext-id record if its group still has >=1 active
    record WITH an External ID (never archive the last copy)
  - never archive a record with sales history (sales_count>0) -> reported
  - groups where every record has an External ID are left untouched (nothing
    to distinguish) -> reported

Usage:
  python3 tools/dedupe_products.py --dry-run
  python3 tools/dedupe_products.py --apply
"""

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    c = OdooClient(); c.authenticate()

    recs = c._execute_kw("product.template", "search_read", [[]],
        {"fields": ["id", "name", "default_code", "create_date"],
         "context": {"active_test": False}})
    # active-only groups by trimmed internal reference
    active = c._execute_kw("product.template", "search_read", [[]],
        {"fields": ["id"], "context": {}})
    active_ids = {r["id"] for r in active}
    byref = defaultdict(list)
    for r in recs:
        if r["default_code"] and r["id"] in active_ids:
            byref[r["default_code"].strip()].append(r)
    groups = {k: v for k, v in byref.items() if len(v) > 1}
    ids = [x["id"] for v in groups.values() for x in v]

    # External IDs present on these records
    extset = set()
    for i in range(0, len(ids), 300):
        rr = c._execute_kw("ir.model.data", "search_read",
            [[["model", "=", "product.template"], ["res_id", "in", ids[i:i+300]]]],
            {"fields": ["res_id"]})
        extset |= {x["res_id"] for x in rr}
    # sales history (computed field -> read, not filter)
    soset = set()
    for i in range(0, len(ids), 300):
        rr = c._execute_kw("product.template", "read",
                           [ids[i:i+300], ["sales_count"]], {})
        soset |= {x["id"] for x in rr if x["sales_count"]}

    to_archive, skip_sales, skip_allext, skip_noext = [], [], [], []
    for ref, members in groups.items():
        haveext = [m for m in members if m["id"] in extset]
        noext = [m for m in members if m["id"] not in extset]
        if not noext:
            skip_allext.append(ref)               # both have ext id
            continue
        if not haveext:
            skip_noext.append(ref)                 # none have ext id (unexpected)
            continue
        for m in noext:
            if m["id"] in soset:
                skip_sales.append((ref, m["id"], m["name"]))
            else:
                to_archive.append((ref, m["id"], m["name"]))

    print(f"duplicate groups: {len(groups)}")
    print(f"records to archive (no ext id): {len(to_archive)}")
    print(f"left as-is: both-have-extid groups={len(skip_allext)}, "
          f"no-extid-at-all groups={len(skip_noext)}, "
          f"sales-history records kept={len(skip_sales)}")
    for ref, rid, nm in skip_sales:
        print(f"  KEPT (has sales) {ref} id={rid} {nm[:40]}")

    if args.dry_run:
        for ref, rid, nm in to_archive[:25]:
            print(f"  archive {ref:<8} id={rid:<6} {nm[:45]}")
        print(f"  ... ({len(to_archive)} total)")
        print("\n(dry-run — nothing archived)")
        return

    arch_ids = [rid for _, rid, _ in to_archive]
    done = 0
    for i in range(0, len(arch_ids), 100):
        c._execute_kw("product.template", "write",
                      [arch_ids[i:i+100], {"active": False}], {})
        done += len(arch_ids[i:i+100])
        print(f"  ...archived {done}/{len(arch_ids)}")
    print(f"\ndone. archived {done} duplicate records.")


if __name__ == "__main__":
    main()
