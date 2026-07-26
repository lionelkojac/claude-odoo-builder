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
  * query — free text (a lamp type, socket/base, colour, application). Recall is
            widened with a marine-electro synonym map (EN <-> NL: fuse/zekering,
            cable/kabel, battery/accu, bulb/lamp, ...) and simple plural folding,
            then degraded gracefully: a strict all-terms match first, and if that
            is thin, a scored any-term match ranked by how many terms hit.
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

# Pure filler — always dropped from a query.
_FILLER = {
    "a", "an", "the", "for", "with", "and", "or", "of", "to", "in", "on", "my",
    "me", "i", "we", "you", "need", "want", "looking", "look", "search", "find",
    "please", "have", "do", "does", "got", "some", "any", "product", "products",
    "item", "items", "type", "kerger", "something", "thing",
    "een", "de", "het", "voor", "met", "en", "of", "van", "ik", "wij", "je",
    "zoek", "zoeken", "nodig", "heb", "hebben", "graag", "iets", "wat",
}

# Common in a lighting catalogue, so low-signal: dropped ONLY when the query has
# other, stronger words (kept if it's all the visitor gave us).
_COMMON = {"lamp", "lamps", "light", "lights", "lampje", "lampen", "lampen"}

# Marine / offshore electro synonyms (EN <-> NL and near-equivalents). Each set
# is expanded as an OR group, so any member matches. Keep terms lowercase.
_SYNONYM_SETS = [
    {"lamp", "bulb", "light", "lampje", "lampen"},
    {"fuse", "zekering", "zekeringen", "smeltveiligheid"},
    {"cable", "kabel", "kabels", "wire", "wiring", "cord", "snoer", "lead"},
    {"battery", "batteries", "accu", "accu's", "batterij", "batterijen"},
    {"switch", "switches", "schakelaar", "schakelaars"},
    {"connector", "connectors", "plug", "stekker", "coupling", "coupler"},
    {"socket", "base", "holder", "fitting", "houder", "voet", "cap"},
    {"relay", "relays", "relais"},
    {"capacitor", "capacitors", "condensator", "condensatoren", "cap"},
    {"contactor", "contactors", "magneetschakelaar"},
    {"breaker", "breakers", "mcb", "automaat", "installatieautomaat"},
    {"transformer", "trafo", "transformator", "transformador"},
    {"resistor", "resistors", "weerstand", "weerstanden"},
    {"terminal", "terminals", "klem", "klemmen"},
    {"waterproof", "watertight", "waterdicht", "sealed"},
    {"navigation", "nav", "navigatie"},
    {"indicator", "signal", "signaal", "indicatie", "pilot"},
    {"tube", "tubes", "tl", "buis"},
    {"button", "pushbutton", "drukknop", "knop"},
    {"gland", "wartel", "doorvoer"},
]
_SYN_INDEX = {}
for _s in _SYNONYM_SETS:
    for _w in _s:
        _SYN_INDEX.setdefault(_w, set()).update(_s)

_cache = {"client": None, "categs": None}


def _client():
    if _cache["client"] is None:
        c = OdooClient()
        c.authenticate()
        _cache["client"] = c
    return _cache["client"]


def client():
    """Public accessor for the shared, authenticated OdooClient (reused by the
    lead-email flow so it doesn't open a second session)."""
    return _client()


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
    return ["|"] * (len(terms) - 1) + [list(t) for t in terms]


def _singulars(word):
    """Naive EN/NL plural folding: add likely singular stems."""
    out = {word}
    for suf in ("en", "s"):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            out.add(word[: -len(suf)])
    return out


def _groups(query):
    """Turn a free-text query into a list of OR-groups (each a set of terms).

    A group is one meaningful word expanded with plurals + synonyms. Filler is
    dropped; low-signal words (lamp/light) are dropped only if stronger words
    remain, so a bare "lamp" still searches.
    """
    toks = re.findall(r"[A-Za-z0-9]+(?:[.\-/][A-Za-z0-9]+)*", query or "")
    strong, common = [], []
    for t in toks:
        low = t.lower()
        if low in _FILLER:
            continue
        if re.search(r"\d", t):          # codes, voltages, sizes — always strong
            strong.append(t)
        elif low in _COMMON:
            common.append(t)
        elif len(low) > 2:
            strong.append(t)
    chosen = strong or common
    groups = []
    for t in chosen:
        low = t.lower()
        grp = set()
        for form in _singulars(low):
            grp.add(form)
            grp |= _SYN_INDEX.get(form, set())
        grp.add(t)
        groups.append(grp)
    return groups


def _term_or(field_terms):
    """Build an OR sub-domain from a list of (field, op, value) leaves."""
    if not field_terms:
        return []
    return ["|"] * (len(field_terms) - 1) + [list(t) for t in field_terms]


def _strict_domain(groups):
    """All groups must match (AND); within a group, any term in name/description."""
    dom = [("website_published", "=", True)]
    for grp in groups:
        leaves = []
        for term in grp:
            leaves += [("name", "ilike", term), ("description", "ilike", term)]
        dom += _term_or(leaves)
    return dom


def _relaxed_domain(groups):
    """Any term from any group (OR) — the wide net for scoring."""
    leaves = []
    for grp in groups:
        for term in grp:
            leaves += [("name", "ilike", term), ("description", "ilike", term)]
    return [("website_published", "=", True)] + _term_or(leaves)


def _score(rec, groups):
    """How many groups the record matches; name hits weigh more than description."""
    name = (rec.get("name") or "").lower()
    desc = (rec.get("description") or "").lower()
    s = 0
    for grp in groups:
        if any(t.lower() in name for t in grp):
            s += 2
        elif any(t.lower() in desc for t in grp):
            s += 1
    return s


def search_products(query=None, code=None, limit=8):
    """Search the live Kerger catalogue. Returns a list of product dicts.

    Exact-code hits first, then free-text: a strict all-terms match, topped up
    (if thin) with a scored any-term match. De-duplicated, capped at `limit`.
    """
    client = _client()
    seen, results = set(), []

    def collect(recs):
        for r in recs:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            results.append(_format(client, r))

    if code and code.strip():
        collect(client._execute_kw("product.template", "search_read",
                [_code_domain(code)], {"fields": _FIELDS, "limit": limit}))

    if query and query.strip() and len(results) < limit:
        groups = _groups(query)
        if groups:
            need = limit - len(results)
            # 1) strict: all terms present
            collect(client._execute_kw("product.template", "search_read",
                    [_strict_domain(groups)], {"fields": _FIELDS, "limit": need}))
            # 2) relaxed + scored: fill remaining with best partial matches
            if len(results) < limit:
                cand = client._execute_kw("product.template", "search_read",
                        [_relaxed_domain(groups)],
                        {"fields": _FIELDS + ["description"], "limit": 60})
                cand = [r for r in cand if r["id"] not in seen]
                cand.sort(key=lambda r: (_score(r, groups), -len(r.get("name") or "")),
                          reverse=True)
                collect(cand[: limit - len(results)])

    return results[:limit]
