"""
Hand a website-advisor conversation to Kerger's sales team.

When a visitor clicks "Send this chat to Kerger", the whole conversation lands
in the livechat operator inbox in Odoo's Discuss app — the same place the team
already sees live-chat conversations — so a colleague can follow up by email.

Mechanism: create a `discuss.channel` of type 'livechat' (linked to the shop
livechat channel), add the operator(s) as members so it appears in their inbox,
and post one message containing the visitor's name/email/question plus the full
transcript. No CRM module is needed (CRM isn't installed); this reuses the
livechat that already exists.

Env:
  KERGER_LIVECHAT_CHANNEL_ID   im_livechat.channel to attach to (default 2 = shop)
"""

import os
import re

from kerger_query import client

SHOP_LC_CHANNEL = int(os.getenv("KERGER_LIVECHAT_CHANNEL_ID", "2"))
NOMATCH_CHANNEL_NAME = "Advisor · unmatched queries"

_cache = {"operators": None, "author": None, "nomatch_channel": None}


def _operators(c):
    """(operator_partner_ids, author_partner_id) for the shop livechat channel.

    Author is the archived "Product advisor" bot partner when present — it must
    differ from the operators so message_post notifies them (a self-authored
    message leaves no unread). Falls back to no explicit author if not found.
    """
    if _cache["operators"] is not None:
        return _cache["operators"], _cache["author"]
    ch = c._execute_kw("im_livechat.channel", "read", [[SHOP_LC_CHANNEL]],
                       {"fields": ["user_ids"]})
    user_ids = ch[0]["user_ids"] if ch else []
    partners = []
    if user_ids:
        users = c._execute_kw("res.users", "read", [user_ids], {"fields": ["partner_id"]})
        partners = [u["partner_id"][0] for u in users if u["partner_id"]]
    env_author = os.getenv("KERGER_ADVISOR_PARTNER_ID")
    if env_author:
        author = int(env_author)
    else:
        # the advisor bot partner is archived, so search with active_test off
        adv = c._execute_kw("res.partner", "search",
                            [[["name", "=", "Product advisor"]]],
                            {"limit": 1, "context": {"active_test": False}})
        author = adv[0] if adv else None
    _cache["operators"], _cache["author"] = partners, author
    return partners, author


def transcript_turns(messages):
    """[(speaker, text)] for real visitor/advisor turns, tool plumbing skipped."""
    turns = []
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role == "user" and isinstance(content, str):
            turns.append(("Visitor", content.strip()))
        elif role == "assistant":
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "".join(getattr(b, "text", "") or (b.get("text", "")
                        if isinstance(b, dict) else "") for b in content)
            text = text.strip()
            if text:
                turns.append(("Advisor", text))
    return turns


def send_enquiry(messages, name="", email="", note=""):
    """Create the livechat channel and post the enquiry as a threaded transcript.

    One message per turn (each renders as its own bubble; message_post over RPC
    escapes HTML, so multi-paragraph in a single message would collapse). All
    messages are authored by the advisor bot partner so the operator, who is a
    member, gets an unread notification. Returns the channel id.
    """
    c = client()
    partners, author = _operators(c)

    who = (name or "").strip() or "Website visitor"
    title = f"Advisor enquiry — {who}"[:120]

    members = [(0, 0, {"partner_id": p}) for p in dict.fromkeys(partners)]
    vals = {"name": title, "channel_type": "livechat"}
    if members:
        vals["channel_member_ids"] = members
    try:
        vals["livechat_channel_id"] = SHOP_LC_CHANNEL
        channel = c._execute_kw("discuss.channel", "create", [vals], {})
    except Exception:
        vals.pop("livechat_channel_id", None)
        channel = c._execute_kw("discuss.channel", "create", [vals], {})

    def post(text):
        kw = {"body": text, "message_type": "comment",
              "subtype_xmlid": "mail.mt_comment"}
        if author:
            kw["author_id"] = author
        c._execute_kw("discuss.channel", "message_post", [[channel]], kw)

    head = f"New enquiry from the website product advisor — {who}"
    contact = f"Contact: {(email or '').strip() or 'no email given'}"
    if (note or "").strip():
        contact += f"  ·  Message: {note.strip()}"
    post(head)
    post(contact)
    for speaker, text in transcript_turns(messages):
        post(f"{speaker}: {text}")
    return channel


def _nomatch_channel(c):
    if _cache["nomatch_channel"] is not None:
        return _cache["nomatch_channel"]
    ids = c._execute_kw("discuss.channel", "search",
                        [[["name", "=", NOMATCH_CHANNEL_NAME],
                          ["channel_type", "=", "channel"]]], {"limit": 1})
    if ids:
        ch = ids[0]
    else:
        partners, _ = _operators(c)
        vals = {"name": NOMATCH_CHANNEL_NAME, "channel_type": "channel"}
        if partners:
            vals["channel_member_ids"] = [(0, 0, {"partner_id": p}) for p in partners]
        ch = c._execute_kw("discuss.channel", "create", [vals], {})
    _cache["nomatch_channel"] = ch
    return ch


def log_no_match(query="", code=""):
    """Record a search that returned nothing, into a dedicated Discuss channel
    the team can review to spot catalogue gaps / missing synonyms. Best-effort:
    never raises (called in a background thread from the chat tool)."""
    try:
        c = client()
        ch = _nomatch_channel(c)
        _, author = _operators(c)
        parts = []
        if (query or "").strip():
            parts.append(f"query: {query.strip()}")
        if (code or "").strip():
            parts.append(f"code: {code.strip()}")
        body = "No match — " + " · ".join(parts) if parts else "No match"
        kw = {"body": body, "message_type": "comment",
              "subtype_xmlid": "mail.mt_comment"}
        if author:
            kw["author_id"] = author
        c._execute_kw("discuss.channel", "message_post", [[ch]], kw)
    except Exception:
        pass
