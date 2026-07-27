"""
Show the stock-status badge on the shop grid and the product page.

Creates two website QWeb view-inheritances (idempotent, keyed) that render a
small coloured pill from x_studio_stock_status (populated by
update_stock_status.py):
  - grid  : overlay on the product card image  (website_sale.products_item)
  - detail: next to the price on the product page (website_sale.product)

Ordering is never affected — the badge is display-only. Colours come from the
CSS block pushed by push_css.py (block "stock_badge"). Reversible with --remove.

Usage:
  python3 tools/push_stock_badge_views.py --apply
  python3 tools/push_stock_badge_views.py --remove
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

# the badge snippet — `product` (product.template) is in scope in both templates
BADGE = """<t t-if="product.x_studio_stock_status">
  <span t-attf-class="kerger-stock kerger-stock-{{product.x_studio_stock_status}}"
        t-att-title="product.x_studio_stock_as_of and ('Stock as of ' + product.x_studio_stock_as_of) or ''">
    <t t-if="product.x_studio_stock_status == 'available'">Available</t>
    <t t-elif="product.x_studio_stock_status == 'low'">Low in stock</t>
    <t t-else="">Out of stock</t>
  </span>
</t>"""

VIEWS = [
    {
        "key": "kerger.stock_badge_grid",
        "name": "Kerger stock badge (shop grid)",
        "inherit": "website_sale.products_item",
        # the card image container uses t-attf-class (dynamic), so hasclass()
        # can't see it at inheritance time — match the t-attf-class attribute.
        "arch": f"""<data>
  <xpath expr="//div[contains(@t-attf-class, 'oe_product_image')]" position="inside">
    {BADGE}
  </xpath>
</data>""",
    },
    {
        "key": "kerger.stock_badge_detail",
        "name": "Kerger stock badge (product page)",
        "inherit": "website_sale.product",
        "arch": f"""<data>
  <xpath expr="//t[@t-call='website_sale.product_price']" position="after">
    <div class="kerger-stock-line mt-2">
      {BADGE}
      <small t-if="product.x_studio_stock_as_of" class="text-muted ms-2">as of <t t-out="product.x_studio_stock_as_of"/></small>
    </div>
  </xpath>
</data>""",
    },
]


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--apply", action="store_true")
    g.add_argument("--remove", action="store_true")
    args = ap.parse_args()

    c = OdooClient(); c.authenticate()

    # always clear our previous views first (idempotent)
    keys = [v["key"] for v in VIEWS]
    old = c._execute_kw("ir.ui.view", "search", [[["key", "in", keys]]], {})
    if old:
        c._execute_kw("ir.ui.view", "unlink", [old], {})
        print(f"removed {len(old)} existing badge view(s)")
    if args.remove:
        print("badge views removed.")
        return

    for v in VIEWS:
        inh = c._execute_kw("ir.ui.view", "search",
                            [[["key", "=", v["inherit"]]]], {"limit": 1})
        if not inh:
            print(f"WARNING: base view {v['inherit']} not found — skipped")
            continue
        vid = c._execute_kw("ir.ui.view", "create", [{
            "name": v["name"], "key": v["key"], "type": "qweb",
            "inherit_id": inh[0], "mode": "extension", "active": True,
            "website_id": 1, "arch_db": v["arch"]}], {})
        print(f"created {v['key']} (id {vid}) inheriting {v['inherit']}")
    print("done — verify the shop and a product page render correctly.")


if __name__ == "__main__":
    main()
