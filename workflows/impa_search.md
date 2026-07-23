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

## Shop search placeholder
The product search bar placeholder reads **"Search (Kerger/IMPA/ISSA/text)"**.
Set in two views (product-search scope only — the site-wide header "all" search
keeps "Search..."):
- `website_sale.search` (view 2017): `placeholder.f="Search (Kerger/IMPA/ISSA/text)"` on the searchbox t-call.
- `website_sale.products` (view 2025): `<t t-else>` fallback after the category placeholder, for the mobile modal + offcanvas inputs.

Backups: `.tmp/backup_view_2017_search.xml`, `.tmp/backup_view_2025_products.xml`.

> **ISSA not searchable yet** — there is no ISSA field on `product.template`
> today (only `x_studio_impa`), so searching an ISSA code returns nothing. The
> placeholder advertises it intentionally (user will add the field later).
>
> **When the ISSA field is created:** name the Studio field **"ISSA"** (→
> `x_studio_issa`). `sync_impa_search.py` auto-detects it (via `CODE_LABELS`) and
> mirrors it into the search index alongside IMPA — just populate the values and
> re-run `python3 tools/sync_impa_search.py`. No code change needed. To make a
> differently-named field searchable, add its label to `CODE_LABELS`.

## ISSA data load (2026-07-22)
Loaded from `kerger_xref_ISSA.csv` (article code → ISSA), matched on
`default_code`. 2,022 CSV codes had ISSA; only **289 matched a live product**
(the other 1,733 are for products not on the site). Populated `x_studio_issa`
(integer) for all 289 and re-ran the sync — ISSA is now searchable.

**Update — full ISSA load (text field).** A **text** field `x_studio_issa_1`
(label "ISSA") was later created, so all ISSA codes were reloaded there,
comma-joined for the 62 multi-code products (e.g. `"7328304, 7320312"`). All
codes — primary and secondary — are now searchable. The old integer
`x_studio_issa` was cleared and is deprecated → **delete it in Studio** so only
one ISSA field remains. `sync_impa_search.py` prefers the char/text field
automatically when a label has both.
> The CSV and load logs live in `.tmp/` (gitignored), not the repo.
