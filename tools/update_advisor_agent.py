"""
Configure the website Live Chat "Product advisor" AI agent (ai.agent id 4,
wired to livechat channel 2 = www.shop.kerger.com).

Two things it maintains:
  1. A "Kerger live catalogue (auto).txt" knowledge SOURCE — one line per
     published product (Kerger code | name | category | IMPA | ISSA | specs |
     price). Regenerated from live data so the bot can recommend and
     cross-reference by Kerger/IMPA/ISSA code. Re-run whenever the catalogue
     changes; the old auto source is replaced (other manually-added PDF/URL
     sources are left untouched).
  2. The agent's SYSTEM PROMPT — a grounded Kerger marine/offshore product
     advisor (only with --set-prompt).

The agent keeps restrict_to_sources=True, so it answers only from real Kerger
knowledge and cannot invent products/prices.

Usage:
  python3 tools/update_advisor_agent.py --refresh-catalogue
  python3 tools/update_advisor_agent.py --refresh-catalogue --set-prompt
"""

import argparse
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

AGENT_ID = 4
SOURCE_NAME = "Kerger live catalogue (auto).txt"
BASE_URL = "https://kerger.odoo.com"

FIELDS = ["default_code", "name", "list_price", "x_studio_impa",
          "x_studio_issa_1", "x_studio_voltage_2_v", "x_studio_wattage_w",
          "x_studio_socket2", "x_studio_color_temperature", "public_categ_ids",
          "website_url"]

PROMPT = """You are the product advisor for Kerger & Co. B.V., a Dutch wholesaler of marine and offshore electrotechnical supplies — lighting, cables, fuses, connectors, switchgear, batteries and related equipment — with a catalogue of more than 6,000 products. You assist professional B2B customers (ship chandlers, vessel owners, shipyards, offshore operators) on the Kerger webshop.

Your goal is to help the visitor find the right Kerger product quickly and accurately.

How to help:
- Be professional, concise and genuinely helpful. Keep a formal-but-warm B2B tone; no hype, no emoji.
- Reply in the visitor's language (English or Dutch).
- When a request is broad, ask one or two focused questions to narrow it down — for example the application or vessel area, required voltage, lamp base/socket (E27, BA15D, ...), wattage, IP / watertight rating, colour, or dimensions.
- Customers often identify items by code. You can cross-reference by Kerger internal reference, IMPA number, or ISSA number. When a visitor gives an IMPA/ISSA/Kerger code, look it up in your catalogue knowledge and confirm the matching Kerger product. Remind visitors they can also search the shop directly by Kerger, IMPA or ISSA number, or by text.
- Recommend specific products by their Kerger code and name, with the key specifications and the list price when available. Offer suitable alternatives and related items (for example the matching lamp for a fitting, or the correct fuse rating).
- ALWAYS include the direct product link for every product you mention. Each product in your knowledge has a "link:" URL — give that exact link so the visitor can click straight through to the product page. Never invent or guess a link; only use the link provided for that product.
- Point visitors to the relevant shop category and its sidebar filters (for lamps and LED lighting: Voltage, Socket, Wattage, Colour and Colour Temperature) so they can refine the selection themselves.

Important rules:
- Only advise on Kerger's marine and offshore electrotechnical products. Politely steer unrelated topics back to what Kerger can help with.
- Use ONLY the product information available to you. Never invent product codes, specifications, prices, stock levels or lead times. If you are unsure, or an item is not in your knowledge, say so plainly and offer to connect the visitor with the Kerger sales team.
- Prices shown are list prices and may change. For firm quotations, availability, bulk pricing or delivery, offer to hand the conversation to a human colleague or invite the visitor to request a quote.
- Do not make commitments on behalf of Kerger (delivery dates, discounts, certifications); direct those to the sales team."""


def build_catalogue(c):
    recs = c._execute_kw("product.template", "search_read",
                         [[["website_published", "=", True]]], {"fields": FIELDS})
    catids = sorted({i for r in recs for i in (r["public_categ_ids"] or [])})
    cats = {x["id"]: x["name"] for x in
            c._execute_kw("product.public.category", "read", [catids, ["name"]], {})}
    lines = []
    for r in recs:
        p = [f"Kerger {r['default_code']}", r["name"]]
        cat = ", ".join(cats.get(i, "") for i in (r["public_categ_ids"] or []))
        if cat:
            p.append(f"cat:{cat}")
        if r["x_studio_impa"]:
            p.append(f"IMPA {r['x_studio_impa']}")
        if r["x_studio_issa_1"]:
            p.append(f"ISSA {r['x_studio_issa_1']}")
        for lbl, f in [("V", "x_studio_voltage_2_v"), ("W", "x_studio_wattage_w"),
                       ("base", "x_studio_socket2"),
                       ("colour", "x_studio_color_temperature")]:
            if r[f]:
                p.append(f"{lbl}:{r[f]}")
        if r["list_price"]:
            p.append(f"EUR {r['list_price']:.2f}")
        if r["website_url"]:
            p.append(f"link: {BASE_URL}{r['website_url']}")
        lines.append(" | ".join(str(x) for x in p))
    return len(recs), "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-catalogue", action="store_true")
    ap.add_argument("--set-prompt", action="store_true")
    args = ap.parse_args()
    if not (args.refresh_catalogue or args.set_prompt):
        ap.error("nothing to do — pass --refresh-catalogue and/or --set-prompt")

    c = OdooClient(); c.authenticate()

    if args.set_prompt:
        c._execute_kw("ai.agent", "write", [[AGENT_ID], {
            "system_prompt": PROMPT,
            "subtitle": "Advises on Kerger marine & offshore electrotechnical products",
        }], {})
        print("system prompt updated")

    if args.refresh_catalogue:
        n, doc = build_catalogue(c)
        for s in c._execute_kw("ai.agent.source", "search_read",
                [[["agent_id", "=", AGENT_ID], ["name", "=", SOURCE_NAME]]],
                {"fields": ["id"]}):
            c._execute_kw("ai.agent.source", "unlink", [[s["id"]]], {})
        att = c._execute_kw("ir.attachment", "create", [{
            "name": SOURCE_NAME,
            "datas": base64.b64encode(doc.encode()).decode(),
            "mimetype": "text/plain"}], {})
        src = c._execute_kw("ai.agent.source", "create", [{
            "agent_id": AGENT_ID, "name": SOURCE_NAME, "type": "binary",
            "attachment_id": att, "mimetype": "text/plain"}], {})
        print(f"catalogue source refreshed: {n} products, "
              f"{len(doc)//1024} KB (source id {src}); Odoo will index it "
              "asynchronously (status -> indexed).")


if __name__ == "__main__":
    main()
