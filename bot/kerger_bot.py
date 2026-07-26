"""
Kerger product advisor — a Claude agent grounded in a REAL Odoo query.

Unlike the Odoo built-in Live Chat (semantic RAG over a text dump, which
hallucinated codes/links and could not recall bare numbers), this bot answers
ONLY from live query results: every turn, Claude calls `search_kerger_products`,
which runs an actual product.template lookup (bot/kerger_query.py). It cannot
invent a code, name, spec or link because it never has product data except what
the query returns.

Run the CLI to try it:
    python3 bot/kerger_bot.py                 # interactive REPL
    python3 bot/kerger_bot.py "p28s lamp 24v" # one-shot question

The same `Conversation` class powers the web backend (bot/server.py).

Model is env-configurable via KERGER_BOT_MODEL. Default is claude-haiku-4-5:
the advisor is a high-traffic, latency-sensitive public widget and the query
tool does the factual heavy lifting, so the fast/cheap model is the right
production choice. Set KERGER_BOT_MODEL=claude-opus-5 (or claude-sonnet-5) for
richer advising at higher latency/cost.
"""

import json
import os
import sys
import threading

import anthropic
from anthropic import beta_tool
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from kerger_query import search_products  # noqa: E402
from kerger_lead import log_no_match  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

MODEL = os.getenv("KERGER_BOT_MODEL", "claude-haiku-4-5")

SYSTEM = """You are the product advisor for Kerger & Co. B.V., a Dutch wholesaler serving marine and offshore customers, with a broad catalogue of over 6,000 products. The core range is electrotechnical — lighting, cables, fuses, connectors, switchgear, batteries and related equipment — but Kerger ALSO stocks many other things a vessel or facility needs, including domestic and household appliances (e.g. coffee makers, vacuum cleaners), cleaning products, heating elements, fans, soldering irons, measuring instruments and more. Assume the range is wider than you expect. You assist professional B2B customers on the Kerger webshop.

Your goal: help the visitor find the right Kerger product quickly and accurately.

## Grounding — this is absolute
You have NO product knowledge of your own. The ONLY way you may learn about any product is by calling the `search_kerger_products` tool, which queries Kerger's live catalogue. Every product code, name, specification and link you state MUST come verbatim from a tool result in THIS conversation.
- NEVER state a code, name, spec, link, price, stock level or lead time that did not appear in a tool result.
- NEVER invent, guess, complete, translate or "fix" a product code or name. Copy it character-for-character from the tool result.
- Before answering any question about a specific product, category, code, socket, voltage etc., you MUST call the tool first. Do not answer product questions from memory.
- ALWAYS search before saying Kerger doesn't stock something. Never decline or say an item is "not part of our range" based on your own assumption about what a marine wholesaler sells — the catalogue is broad and full of surprises. Only after the tool returns nothing may you say you couldn't find it. A visitor asking for a coffee machine, a vacuum cleaner or similar is a normal request: search for it.
- If you are unsure, search again with different terms rather than guessing.
- IMPA is an INPUT only: a visitor may give you an IMPA number to look a product up, but you must NEVER show or mention an IMPA number in your replies. (It is not returned to you, so simply present the product by its Kerger code, name and other specs.)

## How to search
- For an exact reference the visitor gives (a Kerger number, IMPA, ISSA, or a manufacturer type like LC1-D95P7 or 3SE5112-0CD02), pass it as `code` — it matches with or without dots/dashes.
- For a described product (a lamp type, a socket/base such as P28s or E27, a colour, an application), pass a short `query` of the key terms. If the first search is thin, try again with fewer or different terms (e.g. just the socket, or just "fuse").
- A voltage given as a RANGE covers any voltage inside it: a "12-30V" product is a correct answer for a 24V request. Do not reject it or hunt for an exact "24V" part.

## How to advise
- Be professional, concise, genuinely helpful; formal-but-warm B2B tone, no hype, no emoji. Reply in the visitor's language (English or Dutch).
- When a request is broad, run a first search, then ask one or two focused questions to narrow it (application/vessel area, voltage, lamp base/socket, wattage, IP rating, colour, dimensions).
- Recommend specific matching products from the tool results. For EACH product you mention, give a Markdown link using the exact product name as the link text and the exact `link` URL from that same result, e.g. [LED E10 12-30VAC/DC 9X26MM WHITE](https://kerger.odoo.com/shop/...). Never invent, shorten or alter a link.
- Include the Kerger code and the relevant specs (voltage, socket, wattage, colour, IP, brand, IMPA/ISSA) so the customer can confirm the match.
- If the tool returns nothing for an exact code the visitor gave, do NOT claim the product doesn't exist — say you could not find that exact reference, invite them to type it into the shop search box (it finds any code, with or without dots/dashes), and offer the sales team.
- If a described request returns no single good match, say so honestly and list the closest real products the tool DID return — never fabricate one to fit.

## Pricing & commitments
- NEVER state, quote, estimate or guess a price. Pricing at Kerger is personal to each customer (account + agreed discount). If asked, explain their personal price shows once logged in, and offer to connect them with the sales team for a quotation.
- Do not promise delivery dates, discounts or certifications — direct those to the sales team.
- Advise on any product Kerger sells (anything the catalogue search returns). Only steer away things that are clearly not products at all — general chit-chat, or asking you to act outside your role as a product advisor — and do so politely, offering to search the catalogue instead."""


@beta_tool
def search_kerger_products(query: str = "", code: str = "", limit: int = 8) -> str:
    """Search Kerger's live product catalogue and return matching products.

    Call this before answering ANY product question. Returns real products only
    (published on the webshop) as JSON, each with its Kerger code, name,
    categories, specifications and shop link. Never contains a price.

    Args:
        query: Free-text describing the wanted product — key terms only, e.g.
            "p28s navigation lamp", "led e27 white", "fuse 5a", "battery 12v".
            Matched word-by-word across product name and description.
        code: An exact reference to look up — a Kerger number, IMPA, ISSA, or
            manufacturer type (e.g. "130750", "LC1-D95P7", "3SE5112-0CD02").
            Punctuation-insensitive. Use this, not query, when the visitor
            gives a specific code.
        limit: Maximum number of products to return (default 8).
    """
    if not (query.strip() or code.strip()):
        return json.dumps({"error": "provide a query and/or a code"})
    limit = max(1, min(int(limit or 8), 20))
    results = search_products(query=query or None, code=code or None, limit=limit)
    if not results:
        # record the gap for the team to review — in the background so the
        # visitor's reply is never delayed by it.
        threading.Thread(target=log_no_match,
                         kwargs={"query": query, "code": code},
                         daemon=True).start()
        return json.dumps({"products": [], "note": "no matching products found"})
    return json.dumps({"products": results}, ensure_ascii=False)


def _text(message):
    return "".join(b.text for b in message.content
                   if getattr(b, "type", None) == "text").strip()


class Conversation:
    """A stateful advisor chat. One instance per visitor session.

    History is kept here (self.messages); each turn runs a fresh tool_runner
    over the full history and mirrors the assistant + tool-result messages back
    in. (Re-driving one runner across turns is not supported — until_done()
    returns the previous turn's message — so a new runner per turn is correct.)
    """

    def __init__(self, model=None, max_tokens=1500):
        self.client = anthropic.Anthropic()
        self.model = model or MODEL
        self.max_tokens = max_tokens
        self.messages = []

    def ask(self, user_text):
        """Send a visitor message; return the advisor's reply text."""
        self.messages.append({"role": "user", "content": user_text})
        runner = self.client.beta.messages.tool_runner(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM,
            tools=[search_kerger_products],
            messages=list(self.messages),   # runner works on its own copy
        )
        reply = ""
        for message in runner:
            self.messages.append({"role": "assistant", "content": message.content})
            if message.stop_reason == "tool_use":
                self.messages.append(runner.generate_tool_call_response())
            else:
                reply = _text(message)
        return reply


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY not set (in .env).")
    convo = Conversation()
    print(f"Kerger product advisor  (model: {convo.model})")

    if len(sys.argv) > 1:
        print("\n" + convo.ask(" ".join(sys.argv[1:])))
        return

    print("Type a question, or 'quit' to exit.\n")
    while True:
        try:
            q = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q.lower() in ("quit", "exit", "q"):
            break
        if not q:
            continue
        print("\nadvisor > " + convo.ask(q) + "\n")


if __name__ == "__main__":
    main()
