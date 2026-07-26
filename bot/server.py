"""
Web backend for the Kerger product advisor (Railway-hosted).

A tiny Flask app exposing the grounded advisor (bot/kerger_bot.py) over HTTP so
the website chat widget can talk to it. All secrets — the Anthropic API key and
the Odoo credentials — stay server-side here; the browser only ever calls this
backend, never Anthropic or Odoo directly.

Endpoints
  GET  /            -> the embeddable chat widget (bot/widget.html)
  GET  /health      -> {"ok": true}   (Railway health check)
  POST /chat        -> {"session_id": "...", "message": "..."}
                       returns {"session_id": "...", "reply": "..."}

Sessions are kept in memory (session_id -> Conversation), so each visitor keeps
their thread within one server process. This is fine for a single Railway
instance / prototype; move to a shared store (Redis) before scaling out.

Run locally:
    python3 bot/server.py                 # dev server on :8000
Production (Railway uses the Procfile):
    gunicorn -w 2 -b 0.0.0.0:$PORT bot.server:app
"""

import os
import time
import uuid
from threading import Lock

from flask import Flask, jsonify, request, send_from_directory

from kerger_bot import Conversation

HERE = os.path.dirname(__file__)

app = Flask(__name__)

# Which website origins may call /chat from the browser. The widget is embedded
# on the Kerger site, a different origin from this backend, so the browser sends
# a CORS pre-flight. Set ALLOWED_ORIGINS (comma-separated) in the Railway env;
# "*" (the default) is convenient for the prototype but tighten it for prod.
ALLOWED_ORIGINS = [o.strip() for o in
                   os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

# Sessions expire after this idle time so memory doesn't grow unbounded.
SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
MAX_MESSAGE_CHARS = 2000

_sessions = {}          # session_id -> {"convo": Conversation, "seen": ts}
_lock = Lock()


def _origin_allowed(origin):
    return "*" in ALLOWED_ORIGINS or origin in ALLOWED_ORIGINS


@app.after_request
def _cors(resp):
    origin = request.headers.get("Origin", "")
    if origin and _origin_allowed(origin):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return resp


def _reap():
    now = time.time()
    dead = [s for s, v in _sessions.items() if now - v["seen"] > SESSION_TTL]
    for s in dead:
        _sessions.pop(s, None)


@app.route("/health")
def health():
    return jsonify(ok=True)


@app.route("/")
def index():
    return send_from_directory(HERE, "widget.html")


@app.route("/chat", methods=["POST", "OPTIONS"])
def chat():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    session_id = (data.get("session_id") or "").strip()

    if not message:
        return jsonify(error="empty message"), 400
    if len(message) > MAX_MESSAGE_CHARS:
        return jsonify(error="message too long"), 400

    with _lock:
        _reap()
        if not session_id or session_id not in _sessions:
            session_id = uuid.uuid4().hex
            _sessions[session_id] = {"convo": Conversation(), "seen": time.time(),
                                     "lock": Lock()}
        entry = _sessions[session_id]
        entry["seen"] = time.time()

    # Serialise a single visitor's turns so two quick messages don't race the
    # same runner. Different sessions still run concurrently (gunicorn threads).
    try:
        with entry["lock"]:
            reply = entry["convo"].ask(message)
    except Exception as e:  # never leak a stack trace to the widget
        app.logger.exception("advisor error")
        return jsonify(error="advisor temporarily unavailable",
                       detail=str(e)[:200]), 502

    return jsonify(session_id=session_id, reply=reply)


if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ERROR: ANTHROPIC_API_KEY not set (in .env).")
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
