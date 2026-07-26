# Kerger product advisor — query-backed chatbot

A replacement for Odoo's built-in AI Live Chat, which used semantic RAG over a
text dump of the catalogue and kept **hallucinating** — inventing product codes,
names and links, and unable to recall bare numbers (e.g. an IMPA like `796113`).

This bot answers **only from a real Odoo query**. Every turn, Claude calls the
`search_kerger_products` tool, which runs an actual `product.template` lookup
against the live catalogue. The model never has product data except what the
query returns, so it *cannot* invent a code, name, spec or link. Exact-code
lookups (Kerger number, IMPA, ISSA, manufacturer type) work with or without
dots/dashes, and voltage ranges are understood (a 12–30 V product answers a 24 V
request).

## Architecture

```
browser widget ──HTTP──▶ Flask backend ──▶ Claude (tool_runner)
(bot/widget.html)       (bot/server.py)         │
                                                 │ calls tool
                                                 ▼
                                    search_kerger_products
                                    (bot/kerger_query.py)
                                                 │ JSON-RPC
                                                 ▼
                                       Odoo product.template
```

| File | Role |
|------|------|
| `kerger_query.py` | The grounded lookup: builds Odoo domains, returns product dicts (code, name, category, specs, link — **never a price**). Reuses `../tools/odoo_client.py`. |
| `kerger_bot.py`   | The Claude agent: the `@beta_tool search_kerger_products` wrapper, the advisor system prompt, and a stateful `Conversation` class. Also a CLI. |
| `server.py`       | Flask HTTP backend: `/chat`, `/health`, serves the widget. In-memory per-visitor sessions, CORS, per-session locking. |
| `widget.html`     | Brand-styled embeddable chat UI. Renders the bot's Markdown links; talks to `/chat` same-origin. |

All secrets (Anthropic API key, Odoo credentials) stay **server-side**. The
browser only ever calls this backend — never Anthropic or Odoo directly.

## Try it locally

Credentials come from the repo `.env` (`ANTHROPIC_API_KEY` + the `ODOO_*`
vars — already present for the other tools).

```bash
# one-shot question
python3 bot/kerger_bot.py "P28s navigation lamp for 24V"

# interactive REPL
python3 bot/kerger_bot.py

# the web backend + widget (http://localhost:8000)
pip install -r bot/requirements.txt
python3 bot/server.py
```

## Model

Set by `KERGER_BOT_MODEL` (default **`claude-haiku-4-5`**). The advisor is a
high-traffic, latency-sensitive public widget and the query tool does the
factual work, so the fast/cheap model is the right production default. For
richer advising at higher latency/cost, set `KERGER_BOT_MODEL=claude-opus-5`
(or `claude-sonnet-5`).

## Deploy to Railway

The repo root has a `Procfile`, `requirements.txt` and `railway.json`, so the
whole repo deploys as one service (the bot needs `tools/odoo_client.py`).

1. **New Project → Deploy from GitHub repo** → pick this repo, branch
   `claude/setup-zznzsf` (or `main` once merged).
2. **Variables** — add:
   - `ANTHROPIC_API_KEY`
   - `ODOO_URL` = `https://kerger.odoo.com`
   - `ODOO_DB` = `kerger`
   - `ODOO_USER`, `ODOO_PASSWORD` (an API key works)
   - *(optional)* `KERGER_BOT_MODEL`, `ALLOWED_ORIGINS`, `SESSION_TTL_SECONDS`
3. Railway builds with Nixpacks and starts gunicorn (`railway.json` /
   `Procfile`). Health check: `/health`.
4. Open the generated URL — the widget is served at `/`.

`$PORT` is injected by Railway; don't hard-code it.

## Embed on the Kerger website

Simplest, conflict-free option — an iframe pointing at the Railway URL. In Odoo
add an **HTML block** (or a site-wide snippet) with:

```html
<iframe src="https://YOUR-APP.up.railway.app/"
        style="position:fixed;bottom:20px;right:20px;width:400px;height:600px;
               border:0;border-radius:14px;box-shadow:0 12px 40px rgba(11,41,66,.22);
               z-index:9999;background:#fff"
        title="Kerger product advisor"></iframe>
```

Set `ALLOWED_ORIGINS` to the Kerger site origin(s) once embedded (defaults to
`*` for easy testing). A floating open/close launcher button can be layered on
top later; the iframe above is the minimal working embed.

## Notes / next steps

- **Sessions are in-memory**, so they reset on redeploy/restart and don't share
  across multiple instances. Fine for the prototype; move to Redis before
  scaling to more than one worker instance.
- The query tool searches **published** products only (what the customer sees).
- To log conversations for analysis, add persistence in `server.py` (there is
  already `tools/export_chat_history.py` for the old Odoo chat).
