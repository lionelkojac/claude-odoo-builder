# Manage Pages Workflow

## Objective
Reference SOP for all read, update, publish, and inspection operations on existing Odoo website pages. Use this when the user wants to see what's on their site, edit existing content, or change page visibility.

---

## Operations

### List All Pages
```bash
python3 tools/list_pages.py
```
Output: table with ID, URL, name, view_id, published status, homepage flag.
Raw JSON saved to `.tmp/pages_list.json`.

Filter to published pages only:
```bash
python3 tools/list_pages.py --published-only
```

Filter by specific URL:
```bash
python3 tools/list_pages.py --url /about
```

---

### Fetch a Page's HTML Content
```bash
python3 tools/get_page.py --url /about
# or by ID:
python3 tools/get_page.py --id 42
```

Output: arch HTML printed to terminal + saved to `.tmp/page_about.html`.

**Always run `get_page.py` before editing a page** — it creates a backup automatically.

---

### Update a Page's Content
After editing `.tmp/page_about.html` (or a fresh draft):
```bash
python3 tools/push_page.py --update --url /about --file .tmp/page_about.html
```

A backup of the pre-update arch is saved to `.tmp/backup_about.html`.

Full update flow: `get_page.py` → edit file → `validate_html.py` → `push_page.py --update`

---

### Publish a Page
```bash
python3 tools/push_page.py --publish --url /about
```

### Unpublish a Page
```bash
python3 tools/push_page.py --unpublish --url /about
```

---

### Delete a Page
Deletion is intentionally not automated — it's irreversible. Use the Odoo backend:
1. Odoo backend → Website → Pages
2. Select the page → Action → Delete

Or (advanced): Website module → Settings → Pages menu.

---

## Data Model Reference

### `website.page` — page records
| Field | Type | Notes |
|---|---|---|
| `id` | int | Record ID |
| `name` | str | Human-readable title |
| `url` | str | URL path, e.g. `/about` |
| `view_id` | many2one | Linked `ir.ui.view` ID |
| `website_published` | bool | `True` = publicly visible |
| `is_homepage` | bool | `True` = set as homepage |
| `active` | bool | `False` = soft-deleted |
| `website_id` | many2one | Multi-website: which website |

### `ir.ui.view` — the arch (HTML/QWeb content)
| Field | Type | Notes |
|---|---|---|
| `id` | int | Record ID |
| `name` | str | Technical name |
| `arch` | str | Full XML/HTML template content |
| `type` | str | Always `qweb` for website views |
| `key` | str | Dotted key, e.g. `website.page_about` |
| `inherit_id` | many2one | Parent view (for inherited views) |

The arch on `ir.ui.view` is what push_page.py writes to. The `website.page` record
is a thin wrapper that adds URL routing and publish/unpublish control.

---

## Common Search Domains

```python
# All published pages
[("website_published", "=", True)]

# All active (non-deleted) pages
[("active", "=", True)]

# Find by URL
[("url", "=", "/about")]

# Homepage
[("is_homepage", "=", True)]

# Multi-website: pages for website ID 2
[("website_id", "=", 2)]

# Pages containing a keyword in the name
[("name", "ilike", "contact")]
```

These domains can be passed directly to `odoo_client.search_read()` if writing custom scripts.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `list_pages.py` returns 0 results | Check user has "Website" access rights in Odoo Settings |
| Arch appears truncated | Use `get_page.py --id` to fetch by record ID (avoids URL routing issues) |
| Page exists in Odoo but not in list | Check `active=False` — page may be soft-deleted; use domain `[("active","in",[True,False])]` |
| Multi-website: seeing wrong site's pages | Confirm which `website_id` is active; add `--website-id` filter to search |
| Page updates don't appear in browser | Clear Odoo's asset cache: Settings → Technical → Clear Server Assets, or add `?nocache=1` to URL |

---

## Tips for Working Efficiently

1. **Always fetch before editing** — `get_page.py` creates the backup that enables rollback
2. **Keep drafts versioned** — rename `.tmp/draft_about_v2.html` etc. to track iterations
3. **Batch inspection** — `.tmp/pages_list.json` can be parsed to build a site map
4. **Compare versions** — `diff .tmp/backup_about.html .tmp/draft_about.html` to review changes before pushing
5. **Test unpublished** — push first, open `/web/preview?url=/about` in Odoo to preview before publishing

---

## Kerger Site Map & Language Setup (verified 2026-07-22)

**Five website records exist in this DB.** The live site is **website 1** (`kerger`, domain kerger.odoo.com). Websites 2 (kerger2), 3 & 5 (Imported Website) have no domain; website 4 (Kergertest) maps to kerger2.odoo.com. Do not archive other websites' homepages — each website needs its own.

**Global vs website-specific pages:** pages with `website_id=False` are global and serve on any website *unless* shadowed by a website-specific page at the same URL. Archived as dead in 2026-07: global `/` (page 2, shadowed everywhere) and global `/contactus` (page 3, unpublished + shadowed).

**Live pages on website 1:** `/` (page 4, view 916), `/about-us` (5), `/pricing` (6), `/privacy` (7), `/contactus` (10), `/code-of-conduct` (11), `/cookie-policy` (12), plus global `/contactus-thank-you` (page 1 — global, do NOT archive) and global `/privacy` fallback (page 18, shadowed on ws1).

**Languages:** en_US (default) + nl_NL both active. Odoo auto-redirects by browser `Accept-Language` (verified: Dutch browser → 303 to `/nl/`). URL scheme: `/` = English, `/nl/` = Dutch. **Content translations largely don't exist** — both URLs serve the same source text (e.g. `/privacy` is Dutch on both sides). To fix a page: put English in the view source (`arch_db`), then add Dutch via `update_field_translations` on the view for `nl_NL` — never by creating a second page at another URL.

---

## Product page spec fields (eCommerce)

The spec list on `/shop/<product>` pages (Internal Reference, IMPA, Voltage, …)
is **not** a template edit. It's Odoo's built-in eCommerce feature: the template
`website_sale.ecom_show_extra_fields` (view 2132) loops over
`website.shop_extra_field_ids` and shows each field **only where the product has
a value**.

To add/remove/reorder a field on the product page, edit the
`website.sale.extra.field` records (not the view):

```python
# add a field to the product page
imf = c._execute_kw('ir.model.fields','search',
    [[['model','=','product.template'],['name','=','x_studio_wattage_w']]], {})[0]
c._execute_kw('website.sale.extra.field','create',
    [{'field_id': imf, 'sequence': 15, 'website_id': 1}], {})   # label auto-fills
```

- `name`/`label` are read-only (derived from `field_id`); set only `field_id`,
  `sequence`, `website_id`.
- Order = `sequence` then id.
- Backend Studio form layout (view 3022) is separate — editing it does NOT change
  the website page.

**Kerger field notes:** Wattage → `x_studio_wattage_w` (char, populated; the
float `x_studio_wattage2_w` is empty). Product description → `x_studio_description_long`
(multiline **text**, ~50 products, real copy). NOT the "Description " (trailing
space, empty), "Multiline description" (empty), or `x_AI_description`
("Full description", char — contains test junk). Added Wattage (seq 15) and
Description (seq 30) to website 1's product pages 2026-07-22.
