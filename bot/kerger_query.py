"""
Grounded product lookup for the Kerger advisor bot.

This is the ONLY way the bot learns about products: every fact it states comes
from a real Odoo query here, so it cannot invent a code, name, spec or link.

`search_products()` runs against the LIVE Odoo catalogue (product.template) via
the shared OdooClient and returns plain dicts — one per matching published
product — with the code, name, category, specs and shop link. Price is
deliberately never returned (pricing is personal / account-specific).

Two lookup modes, combinable:
  * code  — an exact reference (Kerger number, IMPA, ISSA, manufacturer type or
            barcode). Punctuation-insensitive: "LC1-D95P7" and "LC1D95P7" both
            hit, because codes are mirrored (raw + stripped) into the indexed
            `description` field by tools/sync_impa_search.py.
  * query — free text (a lamp type, socket/base, colour, application). Matched
            token-by-token (AND) across the product name + description, so
            "p28s white 24v" narrows to products whose text has all three.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from odoo_client import OdooClient  # noqa: E402

BASE_URL = os.getenv("KERGER_BASE_URL", "https://kerger.odoo.com")

# Fields read for every hit. Order here is also the order we surface specs in.
_FIELDS = [
    "default_code", "name", "public_categ_ids", "website_url",
    "x_studio_impa", "x_studio_issa_1", "x_studio_manufacturer_code",
    "x_studio_brand", "x_studio_voltage_2_v", "x_studio_wattage_w",
    "x_studio_socket2", "x_studio_color_temperature", "x_studio_ip_rating",
    "x_studio_shape", "x_studio_length_cm", "x_studio_width_cm",
    "x_studio_height_cm", "x_studio_weight_kg_1",
]

# Human labels for the spec fields we expose to the bot.
_SPEC_LABELS = [
    ("x_studio_brand", "brand"),
    ("x_studio_manufacturer_code", "manufacturer_code"),
    ("x_studio_impa", "IMPA"),
    ("x_studio_issa_1", "ISSA"),
    ("x_studio_voltage_2_v", "voltage_V"),
    ("x_studio_wattage_w", "wattage_W"),
    ("x_studio_socket2", "socket"),
    ("x_studio_color_temperature", "colour_temperature"),
    ("x_studio_ip_rating", "IP_rating"),
    ("x_studio_shape", "shape"),
    ("x_studio_length_cm", "length_cm"),
    ("x_studio_width_cm", "width_cm"),
    ("x_studio_height_cm", "height_cm"),
    ("x_studio_weight_kg_1", "weight_kg"),
]

# Stop-words we drop from a free-text query before token matching, so filler
# ("a lamp for the engine room") doesn't over-narrow the AND search.
_STOP = {
    "a", "an", "the", "for", "with", "and", "or", "of", "to", "in", "on",
    "me", "i", "we", "need", "want", "looking", "search", "find", "please",
    "lamp", "light", "product", "products", "item", "type", "kerger",
    "een", "de", "het", "voor", "met", "en", "of", "van", "ik", "zoek",
    "nodig", "lamp", "lampje",
}

_cache = {"client": None, "categs": None}


def _client():
    if _cache["client"] is None:
        c = OdooClient()
        c.authenticate()
        _cache["client"] = c
    return _cache["client"]


def _categ_names(client, ids):
    if not ids:
        return []
    if _cache["categs"] is None:
        rows = client._execute_kw(
            "product.public.category", "search_read", [[]], {"fields": ["name"]})
        _cache["categs"] = {r["id"]: r["name"] for r in rows}
    return [_cache["categs"].get(i) for i in ids if _cache["categs"].get(i)]


def _strip(s):
    return re.sub(r"[^A-Za-z0-9]", "", s or "")


def _format(client, rec):
    """Turn a raw Odoo record into the compact dict the bot sees."""
    specs = {}
    for field, label in _SPEC_LABELS:
        v = rec.get(field)
        if v not in (False, None, "", 0):
            specs[label] = v
    out = {
        "kerger_code": rec.get("default_code"),
        "name": rec.get("name"),
        "categories": _categ_names(client, rec.get("public_categ_ids") or []),
        "specs": specs,
    }
    if rec.get("website_url"):
        out["link"] = f"{BASE_URL}{rec['website_url']}"
    return out


def _code_domain(code):
    """OR-domain matching an exact-ish reference across every code field,
    punctuation-insensitive via the description mirror."""
    raw = code.strip()
    stripped = _strip(raw)
    terms = [
        ("default_code", "=ilike", raw),
        ("barcode", "=", raw),
        ("x_studio_impa", "ilike", raw),
        ("x_studio_issa_1", "ilike", raw),
        ("x_studio_manufacturer_code", "ilike", raw),
        ("description", "ilike", raw),
    ]
    if stripped and stripped != raw:
        terms += [
            ("default_code", "=ilike", stripped),
            ("x_studio_manufacturer_code", "ilike", stripped),
            ("description", "ilike", stripped),
        ]
    dom = ["|"] * (len(terms) - 1) + [list(t) for t in terms]
    return dom


def _tokens(query):
    toks = re.findall(r"[A-Za-z0-9]+(?:[.\-/][A-Za-z0-9]+)*", query or "")
    keep = []
    for t in toks:
        low = t.lower()
        # keep anything with a digit (codes, voltages, sizes) or a non-trivial
        # word that isn't filler
        if re.search(r"\d", t) or (len(low) > 2 and low not in _STOP):
            keep.append(t)
    return keep


def _query_domain(query):
    """AND across tokens; each token must appear in name OR description."""
    dom = [("website_published", "=", True)]
    for tok in _tokens(query):
        dom += ["|", ("name", "ilike", tok), ("description", "ilike", tok)]
    return dom


def search_products(query=None, code=None, limit=8):
    """Search the live Kerger catalogue. Returns a list of product dicts.

    At least one of `query` / `code` should be given. Exact-code hits are
    returned first, then free-text hits, de-duplicated, capped at `limit`.
    """
    client = _client()
    seen, results = set(), []

    def collect(domain, cap):
        recs = client._execute_kw(
            "product.template", "search_read", [domain],
            {"fields": _FIELDS, "limit": cap})
        for r in recs:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            results.append(_format(client, r))

    if code and code.strip():
        collect(_code_domain(code), limit)

    if query and query.strip() and len(results) < limit:
        collect(_query_domain(query), limit - len(results))

    return results[:limit]
