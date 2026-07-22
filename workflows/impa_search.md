# IMPA Search Workflow

## Objective
Make the website shop search (`/shop?search=...`) match on **IMPA codes**, which
live in the Studio field `x_studio_impa` on `product.template`.

## Why it's indirect
- kerger.odoo.com is **Odoo Online (SaaS)** — custom Python modules cannot be
  installed, so `product.template._search_get_detail` (which defines the shop's
  searchable fields) cannot be overridden.
- The shop search DOES index `name`, `default_code`, `description`, and
  `description_sale`. `x_studio_impa` is not indexed.

## Mechanism
Mirror each product's IMPA into the internal **`description`** field (searched by
the shop, but **not shown** on the website — verified). The mirror is wrapped in
a marked paragraph so it is idempotent and removable:

```html
<p class="o_impa_search">IMPA: 794319</p>
```

Chosen over `description_sale` because that field is customer-facing; the user
elected to keep IMPA search-only (hidden).

## The Tool
```bash
python3 tools/sync_impa_search.py --dry-run   # report
python3 tools/sync_impa_search.py             # apply
```
- Idempotent — strips any existing `o_impa_search` block before re-adding, and
  clears the block when a product's IMPA is removed.
- Safe to re-run after catalogue imports or IMPA edits.

## Maintenance
The mirror is a **snapshot**. When IMPA values change in Studio, re-run the tool
to refresh the search index. (No custom-module or auto-sync is installed — an
`ir.cron` scheduled action could automate this, but was not added without
sign-off since it is a standing job on the production DB.)

## Verified 2026-07-22
459 published products, 182 with IMPA. After sync, `/shop?search=<impa>` returns
the matching product; the IMPA code is not visible on product pages.
