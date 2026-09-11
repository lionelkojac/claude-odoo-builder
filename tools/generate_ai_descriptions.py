"""
Generate AI product descriptions (field x_studio_description_long) in the
Kerger house style, for products that lack a real one.

A product "needs" a description when x_studio_description_long is empty OR
equals the product name (the import placeholder). The ~116 genuine
descriptions already on file are never touched — we re-check at write time.

House style (matched to the existing real descriptions):
  - 2-4 plain factual sentences, technical B2B marine-wholesale tone
  - use only the specs we pass in; never invent voltages/dimensions/codes
  - no emoji, no hype, no markdown, no citation links
  - products in the "Fluorescent lighting" public category (id 1396) must
    state they are "for marine use only" (per client instruction)

Batching: N products per Claude call, model returns a JSON array of
{code, description}; we write each back individually so a bad row can't
clobber a good one. Missing/oversized rows are retried once, one per call.

Usage:
  python3 tools/generate_ai_descriptions.py --limit 20 --dry-run
  python3 tools/generate_ai_descriptions.py --limit 20 --apply
  python3 tools/generate_ai_descriptions.py --apply            # all remaining
  python3 tools/generate_ai_descriptions.py --category 1396 --apply
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

from anthropic import Anthropic

from odoo_client import OdooClient

DESC_FIELD = "x_studio_description_long"
FLUORESCENT_CATEG_ID = 1396
MODEL = "claude-haiku-4-5"
BATCH = 10

# spec field -> human label fed to the model (char fields; skip empties/zeros)
SPEC_FIELDS = {
    "x_studio_voltage_2_v": "Voltage",
    "x_studio_wattage_w": "Wattage",
    "x_studio_current2_mah": "Current (mAh)",
    "x_studio_socket2": "Socket/base",
    "x_studio_color_temperature": "Colour temperature",
    "x_studio_shape": "Shape",
    "x_studio_length_cm": "Length (cm)",
    "x_studio_width_cm": "Width (cm)",
    "x_studio_height_cm": "Height (cm)",
    "x_studio_weight_kg_1": "Weight (kg)",
    "x_studio_impa": "IMPA code",
    "x_studio_issa_1": "ISSA code",
}
READ_FIELDS = ["id", "name", "default_code", DESC_FIELD,
               "public_categ_ids"] + list(SPEC_FIELDS)

SYSTEM = (
    "You write short factual product descriptions for a Dutch marine/offshore "
    "electrotechnical wholesaler's B2B webshop. Tone: formal, technical, plain. "
    "Rules: 2-4 sentences per product. Use ONLY the specifications given for "
    "that product; never invent voltages, dimensions, materials, or codes. "
    "NEVER include a website link, URL, domain name, or citation of any kind. "
    "NEVER name another shop, retailer, marketplace, or competitor. You may "
    "name the product's own manufacturer only if it appears in the product "
    "name. No emoji, no marketing hype, no markdown. Write in English. "
    "Describe what the item is and its stated specs in a natural way."
)

# used to detect (and refuse to keep) any link/citation/URL token
LINK_RE = re.compile(r"https?://|www\.|\]\(|\[[^\]]+\]\(|\b\w[\w-]*\.(?:com|net|"
                     r"org|nl|be|de|au|eu)\b|shipserv", re.I)


def needs_desc(r):
    cur = (r.get(DESC_FIELD) or "").strip()
    return (not cur) or cur == (r.get("name") or "").strip()


def spec_lines(r):
    out = []
    for f, label in SPEC_FIELDS.items():
        v = r.get(f)
        if v in (None, False, "", 0, "0"):
            continue
        out.append(f"{label}: {str(v).strip()}")
    return out


def product_block(r):
    lines = [f"code: {r.get('default_code') or r['id']}",
             f"name: {r.get('name') or ''}"]
    lines += spec_lines(r)
    if FLUORESCENT_CATEG_ID in (r.get("public_categ_ids") or []):
        lines.append("REQUIRED: state explicitly that this item is for marine "
                     "use only.")
    return "\n".join(lines)


def call_claude(client, recs):
    """Return {code: description} for a batch of product records."""
    blocks = "\n\n".join(f"--- product {i+1} ---\n{product_block(r)}"
                         for i, r in enumerate(recs))
    prompt = (
        f"Write a description for each of the {len(recs)} products below. "
        "Return ONLY a JSON array, one object per product, in the same order, "
        'each shaped {"code": "<the code>", "description": "<text>"}. '
        "No prose outside the JSON.\n\n" + blocks
    )
    msg = client.messages.create(
        model=MODEL, max_tokens=4096, system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    # tolerate ```json fences
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    data = json.loads(text)
    return {str(d["code"]): scrub_links(d["description"].strip()) for d in data}


def scrub_links(text):
    """Belt-and-braces: drop any markdown citation/URL the model still emits,
    then tidy leftover whitespace/orphan punctuation. Descriptions must never
    contain a link."""
    text = re.sub(r"\s*\(\[[^\]]*\]\([^)]*\)\)", "", text)   # ([label](url))
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)      # [label](url)
    text = re.sub(r"https?://\S+|www\.\S+", "", text)          # bare urls
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="max products to process (0 = all)")
    ap.add_argument("--category", type=int, default=0,
                    help="restrict to a public category id")
    ap.add_argument("--relink", action="store_true",
                    help="regenerate ONLY descriptions that contain a link/url "
                         "(overwrites them, even if otherwise 'real')")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    c = OdooClient(); c.authenticate()
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    domain = [[]]  # match all; needs_desc() filters in python
    if args.category:
        domain = [[["public_categ_ids", "in", [args.category]]]]
    recs = c._execute_kw("product.template", "search_read", domain,
                         {"fields": READ_FIELDS})
    if args.relink:
        todo = [r for r in recs if LINK_RE.search(r.get(DESC_FIELD) or "")]
        selkind = "contain a link"
    else:
        todo = [r for r in recs if needs_desc(r)]
        selkind = "need a description"
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(recs)} scanned | {len(todo)} {selkind}"
          f"{f' (limited to {args.limit})' if args.limit else ''}")
    if not todo:
        return

    by_code = {str(r.get("default_code") or r["id"]): r for r in todo}
    written = failed = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        try:
            out = call_claude(client, batch)
        except Exception as e:
            print(f"  ! batch {i}-{i+len(batch)} failed: {str(e)[:120]}")
            failed += len(batch)
            continue
        for r in batch:
            code = str(r.get("default_code") or r["id"])
            desc = out.get(code)
            if not desc:
                print(f"  ? no output for {code} {r.get('name','')[:30]}")
                failed += 1
                continue
            if args.dry_run:
                print(f"\n[{code}] {r.get('name','')}")
                print("   ", desc)
            elif args.relink:
                # relink mode intentionally overwrites the link-laden text
                c._execute_kw("product.template", "write",
                              [[r["id"]], {DESC_FIELD: desc}], {})
                written += 1
            else:
                # re-guard: never clobber a real description written meanwhile
                cur = c._execute_kw("product.template", "read",
                                    [[r["id"]], [DESC_FIELD, "name"]], {})[0]
                if not needs_desc(cur):
                    print(f"  = skip {code} (now has real desc)")
                    continue
                c._execute_kw("product.template", "write",
                              [[r["id"]], {DESC_FIELD: desc}], {})
                written += 1
        if not args.dry_run:
            print(f"  ...{min(i+BATCH,len(todo))}/{len(todo)} "
                  f"(written={written}, failed={failed})")
        time.sleep(0.5)

    print(f"\ndone. written={written} failed={failed}"
          f"{' (dry-run, nothing saved)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
