"""
Monthly AI insight report for Kerger — turns a month of website signals
(shop + header search terms, searches that returned NO results, and questions
asked to the AI advisor) into an executive briefing with concrete
recommendations, especially products to consider stocking.

generate(dataset) calls Claude with the aggregated month (from
store.report_dataset) and returns Markdown. Used by the server's /report page
and the monthly auto-generation.
"""

import os

import anthropic

MODEL = os.getenv("REPORT_MODEL", "claude-opus-5")

SYSTEM = """You are a demand and merchandising analyst for Kerger & Co. B.V., a Dutch wholesaler of marine and offshore electrotechnical supplies (over 6,000 products). Each month you turn the webshop's behavioural signals into a short, decision-useful briefing for the Kerger commercial team.

You are given, for ONE calendar month:
- search terms customers typed (shop search + the site-wide header search), with counts;
- searches that returned NO results — the strongest signal of unmet demand;
- questions visitors asked the AI product advisor (a sample);
- headline volumes, and the previous month's totals for comparison.

Write the report in Markdown with these sections:

## Executive summary
3–5 sentences: activity level this month, the trend vs last month, and the single most important takeaway.

## What customers were looking for
The main demand themes from the search terms and advisor questions. Group similar terms; cite the actual terms and their counts. Note any clear shift vs last month.

## Products to consider stocking
The heart of the report. Derive candidates mainly from the **no-result searches** and from repeated advisor questions that suggest a product Kerger may not carry. For each candidate: the product/term, how many times it came up, and why it's worth considering (marine/offshore relevance, repeat demand). Prioritise. If a no-result term is likely a product Kerger DOES stock but under a different name/spelling, put it in the next section instead.

## Findability fixes (not new stock)
No-result or low-result searches that probably SHOULD match existing products — synonyms, alternative names, spelling, competitor brand/model cross-references, missing filters. Recommend the specific catalogue / synonym / naming action.

## Advisor & service notes
Common question types, competitor cross-references requested, and how many conversations were handed to sales — with any opportunity this points to.

## Recommended actions
A short, prioritised, concrete checklist the team can act on this month.

Rules:
- Use only the data provided. Cite real terms and real counts; never invent numbers or products.
- If the month's volume is low or the data is sparse, say so plainly and keep the report short and tentative rather than over-reading noise.
- Be concise and specific — this is read by busy commercial people. No filler, no hype, no emoji."""


def _format(d):
    def line(rows, with_zero=False):
        out = []
        for r in rows:
            if with_zero:
                out.append(f"  - {r[0]} — {r[1]}x")
            else:
                flag = " (some/all returned 0 results)" if len(r) > 2 and r[2] == 0 else ""
                out.append(f"  - {r[0]} — {r[1]}x{flag}")
        return "\n".join(out) or "  (none)"

    tm, pm = d["this_month"], d["prev_month"]
    parts = [
        f"MONTH: {d['period']}",
        "",
        "HEADLINE (this month vs previous month):",
        f"  searches: {tm['searches']} (prev {pm['searches']})",
        f"  no-result searches: {tm['zero']} (prev {pm['zero']})",
        f"  advisor conversations: {tm['conversations']} (prev {pm['conversations']})",
        f"  advisor questions: {tm['questions']} (prev {pm['questions']})",
        f"  conversations sent to sales: {tm['leads']} (prev {pm['leads']})",
        "",
        "TOP SEARCH TERMS (term — count):",
        line(d["top_terms"]),
        "",
        "SEARCHES WITH NO RESULTS (term — count) — unmet demand:",
        line(d["zero_terms"], with_zero=True),
        "",
        "SAMPLE OF ADVISOR QUESTIONS:",
        "\n".join(f"  - {q}" for q in d["questions"]) or "  (none)",
    ]
    return "\n".join(parts)


def generate(dataset):
    """Return the Markdown report for a month's dataset."""
    client = anthropic.Anthropic(timeout=600)
    msg = client.messages.create(
        model=MODEL, max_tokens=4000, system=SYSTEM,
        messages=[{"role": "user",
                   "content": "Here is this month's data. Write the briefing.\n\n"
                              + _format(dataset)}])
    return "".join(b.text for b in msg.content
                   if getattr(b, "type", None) == "text").strip()
