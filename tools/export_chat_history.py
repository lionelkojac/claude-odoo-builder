"""
Export Live Chat (Product advisor) conversation history for analysis.

Every website Live Chat session is stored in Odoo as a discuss.channel
(channel_type='livechat') with its messages in mail.message. This dumps them:

  - CSV  (default): one row per message
        session_id, started, livechat_channel, visitor, country, lang,
        msg_date, role (visitor/assistant/system), author, text
  - JSON (--format json): grouped by session with a messages[] list

Role: messages authored by the AI agent's partner are 'assistant'; messages
with no author are 'system' (join/leave notices); everything else 'visitor'.

Usage:
  python3 tools/export_chat_history.py
  python3 tools/export_chat_history.py --format json --out .tmp/chats.json
  python3 tools/export_chat_history.py --channel 2      # only shop.kerger.com
"""

import argparse
import csv
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

AGENT_ID = 4  # Product advisor


def strip_html(body):
    if not body:
        return ""
    t = re.sub(r"<br\s*/?>", "\n", body)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--format", choices=["csv", "json"], default="csv")
    ap.add_argument("--channel", type=int, help="livechat channel id filter")
    args = ap.parse_args()
    out = args.out or f".tmp/chat_history.{args.format}"

    c = OdooClient(); c.authenticate()

    # the AI agent's partner, to label its messages as 'assistant'
    ag = c._execute_kw("ai.agent", "read", [[AGENT_ID], ["partner_id"]], {})
    bot_pid = ag[0]["partner_id"][0] if ag and ag[0]["partner_id"] else None

    dom = [["channel_type", "=", "livechat"]]
    if args.channel:
        dom.append(["livechat_channel_id", "=", args.channel])
    sessions = c._execute_kw("discuss.channel", "search_read", [dom],
        {"fields": ["id", "name", "create_date", "livechat_channel_id",
                    "livechat_lang_id", "country_id"], "order": "create_date"})

    grouped = []
    for s in sessions:
        msgs = c._execute_kw("mail.message", "search_read",
            [[["model", "=", "discuss.channel"], ["res_id", "=", s["id"]]]],
            {"fields": ["author_id", "date", "body", "message_type"],
             "order": "date"})
        rows = []
        for m in msgs:
            aid = m["author_id"][0] if m["author_id"] else None
            # notifications (join/leave, rating) are system; comments are the
            # actual chat — bot partner => assistant, otherwise the visitor
            if m["message_type"] == "notification":
                role = "system"
            elif aid and aid == bot_pid:
                role = "assistant"
            else:
                role = "visitor"
            rows.append({
                "msg_date": m["date"],
                "role": role,
                "author": m["author_id"][1] if m["author_id"] else "",
                "text": strip_html(m["body"]),
            })
        grouped.append({
            "session_id": s["id"],
            "started": s["create_date"],
            "livechat_channel": (s["livechat_channel_id"] or [None, ""])[1],
            "visitor": s["name"],
            "country": (s["country_id"] or [None, ""])[1],
            "lang": (s["livechat_lang_id"] or [None, ""])[1],
            "messages": rows,
        })

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    total_msgs = sum(len(g["messages"]) for g in grouped)
    if args.format == "json":
        with open(out, "w") as f:
            json.dump(grouped, f, indent=2, ensure_ascii=False)
    else:
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["session_id", "started", "livechat_channel", "visitor",
                        "country", "lang", "msg_date", "role", "author", "text"])
            for g in grouped:
                for m in g["messages"]:
                    w.writerow([g["session_id"], g["started"],
                                g["livechat_channel"], g["visitor"], g["country"],
                                g["lang"], m["msg_date"], m["role"], m["author"],
                                m["text"]])
    print(f"exported {len(grouped)} sessions, {total_msgs} messages -> {out}")


if __name__ == "__main__":
    main()
