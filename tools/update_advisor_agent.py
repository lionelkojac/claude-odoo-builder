"""
Configure the website Live Chat "Product advisor" AI agent (ai.agent id 4,
wired to livechat channel 2 = www.shop.kerger.com).

Two things it maintains:
  1. A "Kerger live catalogue (auto).txt" knowledge SOURCE — one line per
     published product (Kerger code | name | category | IMPA | ISSA | specs |
     link). NO price — pricing is personal (account discount), so the advisor
     must not quote it. Regenerated from live data so the bot can recommend and
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

FIELDS = ["default_code", "name", "x_studio_impa",
          "x_studio_issa_1", "x_studio_voltage_2_v", "x_studio_wattage_w",
          "x_studio_socket2", "x_studio_color_temperature", "public_categ_ids",
          "website_url"]

PROMPT = """You are the product advisor for Kerger & Co. B.V., a Dutch wholesaler of marine and offshore electrotechnical supplies — lighting, cables, fuses, connectors, switchgear, batteries and related equipment — with a catalogue of more than 6,000 products. You assist professional B2B customers (ship chandlers, vessel owners, shipyards, offshore operators) on the Kerger webshop.

Your goal is to help the visitor find the right Kerger product quickly and accurately.

How to help:
- Be professional, concise and genuinely helpful. Keep a formal-but-warm B2B tone; no hype, no emoji.
- Reply in the visitor's language (English or Dutch).
- When a request is broad, ask one or two focused questions to narrow it down — for example the application or vessel area, required voltage, lamp base/socket (E27, BA15D, ...), wattage, IP / watertight rating, colour, or dimensions.
- Your main job is to HELP the visitor find the right product. For any descriptive request — a lamp type, a socket/base such as P28s or E27, a function, an application — engage: ask one or two clarifying questions when useful (voltage, wattage, base, colour, size, IP rating, application) and recommend specific matching products from your catalogue, each with its link. NEVER send a descriptive request to the search box; that is your job, not the box's.
- You can cross-reference by Kerger reference, IMPA, ISSA or manufacturer type — look them up in your knowledge and confirm the product.
- ONLY as a last resort, when a visitor gives one specific exact code (a full IMPA/ISSA/Kerger number or manufacturer type) and you genuinely cannot find THAT exact code in your knowledge: do not claim the product doesn't exist — mention they can type that exact code into the shop search box (which finds any code, with or without dots/dashes) and offer the sales team. Use this only for an unfound exact code, never for normal questions.
- Recommend a product ONLY if it appears as a single entry in your catalogue knowledge. Each entry is one line: "Kerger <code> | <name> | ... | link: <url>". EVERY recommendation must BEGIN with that exact "Kerger <code>" copied from the line, followed by the <name> and <url> copied EXACTLY — character for character. If you cannot point to a real "Kerger <code>" line, you do NOT have that product: do not name it.
- NEVER invent or assemble a product. Do not take the base/socket, voltage or size from the visitor's request (or from one line) and attach it to another product's name or link. The code, name, every spec and the link you give must ALL come from ONE real catalogue line. Example of a forbidden mistake: turning a real "LED E10 12-30VAC/DC ... WHITE" line into a non-existent "LED P28S 12-30V ... WHITE" because the visitor asked for P28s.
- If no single line matches the request, say so honestly and list the CLOSEST real products that do exist (each as "Kerger <code> <name>" with its link), e.g. the P28s lamps you actually have — never fabricate one to fit.
- NEVER state, quote, estimate or guess a price. Pricing at Kerger is personal to each customer (it depends on their account and agreed discount), so you do not know the visitor's price and must not imply one. If asked about price, explain that their personal price is shown once they are logged in to their account, and offer to connect them with the sales team for a quotation.
- A product whose voltage is a RANGE (e.g. "12-30 V") is suitable for ANY voltage inside that range: a request for 24 V is correctly met by a 12-30 V product. Do not search for, or invent, an exact "24 V" product when a covering range already exists.
- ALWAYS give a clickable link for every product you mention, as a Markdown link using the exact product name as the text and the exact "link:" URL from that same entry, e.g. [LED E10 12-30VAC/DC 9X26MM WHITE](https://kerger.odoo.com/shop/...). Never invent, shorten or guess a link.
- Point visitors to the relevant shop category and its sidebar filters (for lamps and LED lighting: Voltage, Socket, Wattage, Colour and Colour Temperature) so they can refine the selection themselves.

Important rules:
- Only advise on Kerger's marine and offshore electrotechnical products. Politely steer unrelated topics back to what Kerger can help with.
- Use ONLY the product information available to you. NEVER invent or guess a product code, product name, specification, link, stock level or lead time. If no catalogue entry exactly matches the request, say clearly that you could not find an exact match and offer to connect the visitor with the Kerger sales team — it is far better to admit that than to give a wrong name or link.
- Before sending a recommendation, double-check that the exact name and link you are about to give both appear together on one real catalogue line. If they do not, do not send it.
- For pricing, firm quotations, availability, bulk pricing or delivery, offer to hand the conversation to a human colleague or invite the visitor to request a quote — never give figures yourself.
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
        # NOTE: price is deliberately NOT included — pricing is personal
        # (account-specific with the customer's discount), so the advisor must
        # never quote it.
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
