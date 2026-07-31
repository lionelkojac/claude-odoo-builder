"""
Import a stock/price file into Odoo — sales price, quantity on hand, and the
stock-status badge — matched by Kerger number (default_code).

This is the single importer used both for manual uploads and for the automated
Kerridge SFTP pipeline (sftp/). It accepts CSV (auto-detects , or ;) or the
legacy .xls export, columns detected by header:
    Product | Sales Price | Max Stock | Min Stock | Available stock

What it writes (choose with flags; default = all three):
  --price   list_price       <- "Sales Price"
  --qty     on-hand quantity <- "Available stock"  (stock.quant at
            ODOO_STOCK_LOCATION_ID; storable products only)
  --status  x_studio_stock_status badge (available / low / out) + as-of date
            (rule: out if avail<=0; low if avail < max(5, 50% of Min Stock))

Usage:
  python3 tools/import_stock_file.py --file STOCK.csv --dry-run
  python3 tools/import_stock_file.py --file STOCK.csv --apply
  python3 tools/import_stock_file.py --file STOCK.csv --apply --qty --status
"""

import argparse
import csv
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient
from update_stock_status import status_of, ensure_field, STATUS_FIELD, ASOF_FIELD


def _num(v):
    try:
        return float(v if v not in (None, "") else 0)
    except (TypeError, ValueError):
        # NL decimals in text ("1.234,56")
        try:
            return float(str(v).replace(".", "").replace(",", "."))
        except ValueError:
            return 0.0


def load(path):
    """Return [{code, price, min, avail}] from a CSV or .xls, by header names."""
    def pick(header, *names):
        low = [h.strip().lower() for h in header]
        for n in names:
            if n in low:
                return low.index(n)
        return None

    rows = []
    if path.lower().endswith((".xls", ".xlsx")):
        import xlrd
        sh = xlrd.open_workbook(path).sheet_by_index(0)
        header = [str(sh.cell_value(0, c)) for c in range(sh.ncols)]
        ic = pick(header, "product", "kerger number", "kerger")
        ip = pick(header, "sales price", "price", "verkprijs")
        imn = pick(header, "min stock", "minimum stock")
        iav = pick(header, "available stock", "available", "stock", "on hand")
        for r in range(1, sh.nrows):
            code = str(sh.cell_value(r, ic)).strip() if ic is not None else ""
            if not code:
                continue
            rows.append({"code": code,
                         "price": _num(sh.cell_value(r, ip)) if ip is not None else None,
                         "min": _num(sh.cell_value(r, imn)) if imn is not None else 0,
                         "avail": _num(sh.cell_value(r, iav)) if iav is not None else None})
        return rows

    with open(path, encoding="utf-8-sig") as fh:
        head = fh.readline()
        delim = ";" if head.count(";") > head.count(",") else ","
        fh.seek(0)
        rd = csv.reader(fh, delimiter=delim)
        header = next(rd, [])
        ic = pick(header, "product", "kerger number", "kerger")
        ip = pick(header, "sales price", "price", "verkprijs")
        imn = pick(header, "min stock", "minimum stock")
        iav = pick(header, "available stock", "available", "stock", "on hand")
        for x in rd:
            if ic is None or len(x) <= ic or not x[ic].strip():
                continue
            rows.append({"code": x[ic].strip(),
                         "price": _num(x[ip]) if ip is not None and len(x) > ip else None,
                         "min": _num(x[imn]) if imn is not None and len(x) > imn else 0,
                         "avail": _num(x[iav]) if iav is not None and len(x) > iav else None})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--price", action="store_true", help="update Sales Price")
    ap.add_argument("--qty", action="store_true", help="update quantity on hand")
    ap.add_argument("--status", action="store_true", help="update the stock badge")
    ap.add_argument("--as-of", help="badge date (YYYY-MM-DD); default today")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    # default: do everything
    do_price = args.price or not (args.price or args.qty or args.status)
    do_qty = args.qty or not (args.price or args.qty or args.status)
    do_status = args.status or not (args.price or args.qty or args.status)
    if not os.path.exists(args.file):
        sys.exit(f"ERROR: file not found: {args.file}")

    as_of = args.as_of or datetime.date.today().isoformat()
    loc = int(os.getenv("ODOO_STOCK_LOCATION_ID", "0"))
    rows = load(args.file)
    c = OdooClient(); c.authenticate()

    codes = [r["code"] for r in rows]
    tmpl = c._execute_kw("product.template", "search_read",
        [[["default_code", "in", codes]]],
        {"fields": ["id", "default_code", "list_price"]})
    t_by = {str(p["default_code"]).strip(): p for p in tmpl}
    # variants carry the stock
    variants = c._execute_kw("product.product", "search_read",
        [[["default_code", "in", codes]]],
        {"fields": ["id", "default_code", "is_storable"]})
    v_by = {str(p["default_code"]).strip(): p for p in variants}

    price_plan, status_plan, qty_plan = {}, {}, {}
    unmatched = non_storable = 0
    for r in rows:
        p = t_by.get(r["code"])
        if not p:
            unmatched += 1
            continue
        if do_price and r["price"] is not None and abs((p["list_price"] or 0) - r["price"]) > 0.005:
            price_plan[p["id"]] = round(r["price"], 2)
        if do_status and r["avail"] is not None:
            status_plan[p["id"]] = status_of(r["min"], r["avail"])
        if do_qty and r["avail"] is not None:
            v = v_by.get(r["code"])
            if v and v["is_storable"]:
                qty_plan[v["id"]] = r["avail"]
            elif v:
                non_storable += 1

    print(f"file rows: {len(rows)} | matched: {len(t_by)} | unmatched: {unmatched}")
    print(f"price updates: {len(price_plan) if do_price else 'skip'} | "
          f"qty updates: {len(qty_plan) if do_qty else 'skip'}"
          f"{f' (+{non_storable} non-storable skipped)' if non_storable else ''} | "
          f"status writes: {len(status_plan) if do_status else 'skip'}")
    if args.dry_run:
        for pid, pr in list(price_plan.items())[:8]:
            print(f"  price [{pid}] -> {pr}")
        print("(dry-run — nothing written)")
        return

    # ---- prices ----
    if do_price:
        from collections import defaultdict
        grp = defaultdict(list)
        for pid, pr in price_plan.items():
            grp[pr].append(pid)
        for pr, ids in grp.items():
            for i in range(0, len(ids), 200):
                c._execute_kw("product.template", "write", [ids[i:i+200], {"list_price": pr}], {})
    # ---- status ----
    if do_status and status_plan:
        ensure_field(c, STATUS_FIELD, "Stock status")
        ensure_field(c, ASOF_FIELD, "Stock as of")
        from collections import defaultdict
        grp = defaultdict(list)
        for pid, st in status_plan.items():
            grp[st].append(pid)
        for st, ids in grp.items():
            for i in range(0, len(ids), 200):
                c._execute_kw("product.template", "write",
                    [ids[i:i+200], {STATUS_FIELD: st, ASOF_FIELD: as_of}], {})
    # ---- quantity on hand ----
    if do_qty and qty_plan and loc:
        vids = list(qty_plan)
        exist = c._execute_kw("stock.quant", "search_read",
            [[["location_id", "=", loc], ["product_id", "in", vids]]],
            {"fields": ["id", "product_id"]})
        q_by = {q["product_id"][0]: q["id"] for q in exist}
        qids, to_create = [], []
        for vid, qty in qty_plan.items():
            if vid in q_by:
                c._execute_kw("stock.quant", "write",
                    [[q_by[vid]], {"inventory_quantity": qty}], {})
                qids.append(q_by[vid])
            else:
                to_create.append({"product_id": vid, "location_id": loc,
                                  "inventory_quantity": qty})
        if to_create:
            new = c._execute_kw("stock.quant", "create", [to_create], {})
            qids += new if isinstance(new, list) else [new]
        for i in range(0, len(qids), 200):
            c._execute_kw("stock.quant", "action_apply_inventory", [qids[i:i+200]], {})
    elif do_qty and qty_plan and not loc:
        print("  ! ODOO_STOCK_LOCATION_ID not set — quantity NOT updated")

    print(f"done — prices {len(price_plan) if do_price else 0}, "
          f"qty {len(qty_plan) if do_qty else 0}, status {len(status_plan) if do_status else 0} "
          f"(as of {as_of})")


if __name__ == "__main__":
    main()
