"""
Import product images and documents from a media CSV (URLs on the Kerger file
host) into Odoo, matched by Kerger number.

CSV columns (detected by header):
  Kerger number, Name, EAN, Catalogue picture 1, Catalogue picture 2,
  Document 1 .. Document 4

These CSVs contain only the products that need NEW media, so by default the
tool OVERWRITES. Per matched product:
  - "Catalogue picture 1"  -> main product image (image_1920). Replaces any
    existing image (pass --keep-existing-image to skip products that already
    have one).
  - "Catalogue picture 2"  -> an extra gallery image (product.image), added
    unless one with the same filename already exists (no duplicates on re-run).
  - "Document N" (any)     -> a product.document shown on the product page. A
    document with the same filename is REPLACED (updated); a new filename is
    added.

Usage:
  python3 tools/import_product_media.py --file media.csv --dry-run
  python3 tools/import_product_media.py --file media.csv --apply
  python3 tools/import_product_media.py --file media.csv --apply --keep-existing-image
"""

import argparse
import base64
import csv
import os
import sys
import urllib.parse

import requests

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

PIC_COLS = ["Catalogue picture 1", "Catalogue picture 2"]
DOC_COLS = ["Document 1", "Document 2", "Document 3", "Document 4"]


def filename_from_url(url, fallback):
    """Pull a human filename out of the ?k=<path> query param."""
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        k = q.get("k", [""])[0]
        base = os.path.basename(k)
        return base or fallback
    except Exception:
        return fallback


def fetch(url):
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "").split(";")[0].strip()


def load(path):
    with open(path, encoding="utf-8-sig") as fh:
        head = fh.readline()
        delim = ";" if head.count(";") > head.count(",") else ","
        fh.seek(0)
        # utf-8-sig again handles a BOM at the very start
        return list(csv.DictReader(fh, delimiter=delim))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--keep-existing-image", action="store_true",
                    help="do NOT overwrite a main image that already exists "
                         "(default: overwrite, since the CSV holds only new pictures)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.file):
        sys.exit(f"ERROR: file not found: {args.file}")

    rows = load(args.file)
    code_col = next((k for k in rows[0] if k.strip().lower().startswith("kerger")), None) if rows else None
    if not code_col:
        sys.exit("ERROR: no 'Kerger number' column found")

    c = OdooClient(); c.authenticate()
    codes = [str(r[code_col]).strip() for r in rows if r.get(code_col)]
    prods = c._execute_kw("product.template", "search_read",
        [[["default_code", "in", codes]]], {"fields": ["id", "default_code", "image_1920"]})
    byc = {str(p["default_code"]).strip(): p for p in prods}

    plan_main, plan_extra, plan_doc, unmatched = [], [], [], []
    for r in rows:
        code = str(r.get(code_col, "")).strip()
        if not code:
            continue
        if code not in byc:
            unmatched.append(code)
            continue
        p = byc[code]
        p1 = (r.get(PIC_COLS[0]) or "").strip()
        if p1.startswith("http") and not (args.keep_existing_image and p["image_1920"]):
            plan_main.append((p["id"], code, p1))
        p2 = (r.get(PIC_COLS[1]) or "").strip()
        if p2.startswith("http"):
            plan_extra.append((p["id"], code, p2))
        for dc in DOC_COLS:
            u = (r.get(dc) or "").strip()
            if u.startswith("http"):
                plan_doc.append((p["id"], code, u))

    print(f"rows: {len(rows)} | matched: {len(byc)} | unmatched: {sorted(set(unmatched))}")
    print(f"main images: {len(plan_main)} | extra images: {len(plan_extra)} | documents: {len(plan_doc)}")

    if args.dry_run:
        for pid, code, u in plan_main[:20]:
            print(f"  main  {code}: {filename_from_url(u, code)}")
        for pid, code, u in plan_doc[:20]:
            print(f"  doc   {code}: {filename_from_url(u, code)}")
        print("\n(dry-run — nothing written)")
        return

    ids = [p["id"] for p in prods]
    # existing product.documents keyed by (product, filename) -> [doc ids],
    # so a same-name document can be REPLACED (updated), not skipped.
    existing_docs = {}
    for d in c._execute_kw("product.document", "search_read",
            [[["res_model", "=", "product.template"], ["res_id", "in", ids]]],
            {"fields": ["id", "res_id", "name"]}):
        existing_docs.setdefault((d["res_id"], d["name"]), []).append(d["id"])
    # existing gallery image names per product -> skip duplicate extra images
    existing_imgs = {}
    for im in c._execute_kw("product.image", "search_read",
            [[["product_tmpl_id", "in", ids]]], {"fields": ["product_tmpl_id", "name"]}):
        existing_imgs.setdefault(im["product_tmpl_id"][0], set()).add(im["name"])

    img_ok = extra_ok = doc_ok = fail = 0
    for pid, code, u in plan_main:          # overwrite the main image
        try:
            data, _ = fetch(u)
            c._execute_kw("product.template", "write",
                [[pid], {"image_1920": base64.b64encode(data).decode()}], {})
            img_ok += 1
        except Exception as e:
            fail += 1; print(f"  ! main {code}: {str(e)[:70]}")
    for pid, code, u in plan_extra:         # add gallery image unless already present
        name = filename_from_url(u, code)
        if name in existing_imgs.get(pid, set()):
            continue
        try:
            data, _ = fetch(u)
            c._execute_kw("product.image", "create", [{
                "name": name, "product_tmpl_id": pid,
                "image_1920": base64.b64encode(data).decode()}], {})
            existing_imgs.setdefault(pid, set()).add(name)
            extra_ok += 1
        except Exception as e:
            fail += 1; print(f"  ! extra {code}: {str(e)[:70]}")
    for pid, code, u in plan_doc:           # replace same-name doc, else add
        name = filename_from_url(u, f"{code}.pdf")
        old = existing_docs.get((pid, name))
        if old:
            try:
                c._execute_kw("product.document", "unlink", [old], {})  # cascades attachment
            except Exception:
                pass
            existing_docs[(pid, name)] = []
        try:
            data, mime = fetch(u)
            # standalone attachment (no res_model) — the product.document below
            # provides the linkage. Setting res_model here makes Odoo auto-create
            # a second (hidden) product.document, i.e. a duplicate.
            att = c._execute_kw("ir.attachment", "create", [{
                "name": name, "datas": base64.b64encode(data).decode(),
                "mimetype": mime or "application/pdf"}], {})
            c._execute_kw("product.document", "create", [{
                "name": name, "ir_attachment_id": att,
                "res_model": "product.template", "res_id": pid,
                "shown_on_product_page": True}], {})
            doc_ok += 1
        except Exception as e:
            fail += 1; print(f"  ! doc {code}: {str(e)[:70]}")

    print(f"\ndone. main images {img_ok}, extra images {extra_ok}, documents {doc_ok}, failed {fail}")


if __name__ == "__main__":
    main()
