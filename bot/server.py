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

import datetime
import os
import time
import uuid
from threading import Lock, Thread

from flask import Flask, Response, jsonify, request, send_from_directory

from kerger_bot import Conversation
from kerger_lead import send_enquiry
import store
import report

HERE = os.path.dirname(__file__)

# Dashboard is protected by HTTP basic auth. Set DASHBOARD_PASSWORD in the env to
# enable /dashboard; without it the route returns 503 (never open by accident).
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "kerger")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
# Email each monthly report here. Defaults to the Kerger company address;
# override with the REPORT_EMAIL env var on Railway (e.g. a sales alias).
REPORT_EMAIL = os.getenv("REPORT_EMAIL", "lionelkaptein@gmail.com")

app = Flask(__name__)
store.init()

# Which website origins may call /chat from the browser. The widget is embedded
# on the Kerger site, a different origin from this backend, so the browser sends
# a CORS pre-flight. Set ALLOWED_ORIGINS (comma-separated) in the Railway env;
# "*" (the default) is convenient for the prototype but tighten it for prod.
ALLOWED_ORIGINS = [o.strip() for o in
                   os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

# Sessions expire after this idle time so memory doesn't grow unbounded.
SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
MAX_MESSAGE_CHARS = 2000

# Basic per-IP rate limits (sliding window) so the open endpoint can't be used
# to burn API credit or spam the sales inbox. In-memory per worker.
CHAT_LIMIT = int(os.getenv("CHAT_RATE_LIMIT", "40"))       # per window
CHAT_WINDOW = int(os.getenv("CHAT_RATE_WINDOW", "300"))    # seconds
LEAD_LIMIT = int(os.getenv("LEAD_RATE_LIMIT", "5"))
LEAD_WINDOW = int(os.getenv("LEAD_RATE_WINDOW", "3600"))

_sessions = {}          # session_id -> {"convo": Conversation, "seen": ts, "lock": Lock}
_hits = {}              # (bucket, ip) -> [timestamps]
_lock = Lock()


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "?"


def _rate_ok(bucket, ip, limit, window):
    now = time.time()
    with _lock:
        key = (bucket, ip)
        hits = [t for t in _hits.get(key, []) if now - t < window]
        if len(hits) >= limit:
            _hits[key] = hits
            return False
        hits.append(now)
        _hits[key] = hits
        # opportunistic cleanup of stale buckets
        if len(_hits) > 5000:
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > window]:
                _hits.pop(k, None)
        return True


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

    if not _rate_ok("chat", _client_ip(), CHAT_LIMIT, CHAT_WINDOW):
        return jsonify(error="Too many messages — please slow down a moment."), 429

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

    store.log_chat(session_id, "user", message, entry["convo"].model)
    store.log_chat(session_id, "assistant", reply, entry["convo"].model)
    return jsonify(session_id=session_id, reply=reply)


@app.route("/lead", methods=["POST", "OPTIONS"])
def lead():
    """Hand the current conversation to Kerger's livechat operator inbox."""
    if request.method == "OPTIONS":
        return ("", 204)

    if not _rate_ok("lead", _client_ip(), LEAD_LIMIT, LEAD_WINDOW):
        return jsonify(error="You've already sent this. The team will be in touch."), 429

    data = request.get_json(silent=True) or {}
    session_id = (data.get("session_id") or "").strip()
    name = (data.get("name") or "").strip()[:120]
    email = (data.get("email") or "").strip()[:200]
    note = (data.get("note") or "").strip()[:1000]

    with _lock:
        entry = _sessions.get(session_id)
    if not entry:
        return jsonify(error="no active conversation to send"), 400

    messages = entry["convo"].messages
    if not messages:
        return jsonify(error="the conversation is empty"), 400

    try:
        with entry["lock"]:
            send_enquiry(messages, name=name, email=email, note=note)
    except Exception as e:
        app.logger.exception("lead error")
        return jsonify(error="could not send to Kerger", detail=str(e)[:200]), 502

    store.log_chat(session_id, "lead", "conversation sent to sales")
    return jsonify(ok=True)


@app.route("/search-log", methods=["POST", "OPTIONS"])
def search_log():
    """Record a shop (or chat) search term + result count. Called from the Odoo
    shop via navigator.sendBeacon (fire-and-forget, no CORS pre-flight)."""
    if request.method == "OPTIONS":
        return ("", 204)
    import json
    try:
        data = json.loads(request.get_data() or b"{}")
    except Exception:
        data = request.get_json(silent=True) or {}
    term = (data.get("term") or "").strip()
    if not term or len(term) > 500:
        return ("", 204)
    results = data.get("results")
    results = int(results) if isinstance(results, (int, float)) else None
    store.log_search(term, results=results, logged_in=data.get("logged_in"),
                     source=(data.get("source") or "shop"))
    return ("", 204)


def _dash_auth():
    a = request.authorization
    return (DASHBOARD_PASSWORD and a and a.username == DASHBOARD_USER
            and a.password == DASHBOARD_PASSWORD)


@app.route("/dashboard")
def dashboard():
    if not DASHBOARD_PASSWORD:
        return ("Dashboard disabled — set DASHBOARD_PASSWORD in the environment.", 503)
    if not _dash_auth():
        return Response("Authentication required", 401,
                        {"WWW-Authenticate": 'Basic realm="Kerger insights"'})
    if not store.enabled():
        return ("No database connected — add a Postgres database on Railway "
                "(it sets DATABASE_URL) and reload.", 200)
    _autogenerate_due()   # lazily kick off last month's report if it's due
    try:
        days = max(1, min(int(request.args.get("days", 30)), 365))
    except ValueError:
        days = 30
    return render_dashboard(store.dashboard_data(days))


def _md_to_html(text):
    """Minimal, safe Markdown -> HTML for the AI report (escape first)."""
    import html
    import re
    def inline(s):
        s = html.escape(s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return s
    out, mode = [], None
    for raw in (text or "").split("\n"):
        t = raw.rstrip()
        if re.match(r"^#{1,6} ", t):
            if mode:
                out.append(f"</{mode}>"); mode = None
            lvl = len(t) - len(t.lstrip("#"))
            out.append(f"<h{lvl}>{inline(t.lstrip('# ').strip())}</h{lvl}>")
        elif re.match(r"^\s*\d+\.\s+", t):
            if mode != "ol":
                if mode: out.append(f"</{mode}>")
                out.append("<ol>"); mode = "ol"
            item = re.sub(r"^\s*\d+\.\s+", "", t)
            out.append(f"<li>{inline(item)}</li>")
        elif re.match(r"^\s*[-*]\s+", t):
            if mode != "ul":
                if mode: out.append(f"</{mode}>")
                out.append("<ul>"); mode = "ul"
            item = re.sub(r"^\s*[-*]\s+", "", t)
            out.append(f"<li>{inline(item)}</li>")
        elif t.strip() == "":
            if mode: out.append(f"</{mode}>"); mode = None
        else:
            if mode: out.append(f"</{mode}>"); mode = None
            out.append(f"<p>{inline(t)}</p>")
    if mode:
        out.append(f"</{mode}>")
    return "\n".join(out)


def _prev_month(ref=None):
    ref = ref or datetime.date.today()
    y, m = (ref.year, ref.month - 1) if ref.month > 1 else (ref.year - 1, 12)
    return y, m, f"{y}-{m:02d}"


def _email_report(month, md):
    if not REPORT_EMAIL:
        return
    try:
        from kerger_query import client
        c = client()
        # create the outgoing mail; Odoo's mail queue delivers it (calling
        # .send() over RPC returns no result and isn't needed).
        c._execute_kw("mail.mail", "create", [{
            "subject": f"Kerger — website insights report {month}",
            "email_to": REPORT_EMAIL,
            "body_html": _md_to_html(md),
            "auto_delete": False}], {})
        # push the queue so it sends now (this RPC returns no result -> ignore)
        try:
            c._execute_kw("mail.mail", "process_email_queue", [], {})
        except Exception:
            pass
    except Exception:
        app.logger.exception("report email failed")


def _generate_month(month_str, year, month):
    try:
        content = report.generate(store.report_dataset(year, month))
        store.save_report(month_str, content)
        _email_report(month_str, content)
    except Exception:
        app.logger.exception("report generation failed")
        store.save_report(month_str, "_Report generation failed — try Generate again._")


def _kickoff(month_str, year, month):
    """Claim + generate a month in the background (once, across workers)."""
    if store.claim_report(month_str):
        Thread(target=_generate_month, args=(month_str, year, month), daemon=True).start()
        return True
    return False


def _autogenerate_due():
    """Ensure last month's report exists (the monthly automation). Skips months
    that had no activity so empty reports aren't generated/emailed."""
    if not store.enabled():
        return
    y, m, ms = _prev_month()
    if store.get_report(ms) is not None:
        return
    ds = store.report_dataset(y, m)
    if ds["this_month"]["searches"] == 0 and ds["this_month"]["questions"] == 0:
        return
    _kickoff(ms, y, m)


@app.route("/report")
def report_page():
    if not DASHBOARD_PASSWORD:
        return ("Reports disabled — set DASHBOARD_PASSWORD in the environment.", 503)
    if not _dash_auth():
        return Response("Authentication required", 401,
                        {"WWW-Authenticate": 'Basic realm="Kerger insights"'})
    if not store.enabled():
        return ("No database connected — add a Postgres database on Railway.", 200)

    month = request.args.get("month")
    if not month:
        _, _, month = _prev_month()
    try:
        y, m = int(month[:4]), int(month[5:7])
    except (ValueError, IndexError):
        y, m, month = _prev_month()
    rep = store.get_report(month)
    if rep is None:
        _kickoff(month, y, m)
        rep = {"status": "generating"}
    return render_report(month, rep, store.list_reports())


@app.route("/report/generate", methods=["POST"])
def report_generate():
    if not DASHBOARD_PASSWORD or not _dash_auth():
        return Response("Authentication required", 401,
                        {"WWW-Authenticate": 'Basic realm="Kerger insights"'})
    month = (request.form.get("month") or request.args.get("month") or "").strip()
    if not month:
        _, _, month = _prev_month()
    try:
        y, m = int(month[:4]), int(month[5:7])
    except (ValueError, IndexError):
        return ("bad month", 400)
    store.delete_report(month)          # force regenerate
    _kickoff(month, y, m)
    return Response("", 303, {"Location": f"/report?month={month}"})


def render_report(month, rep, months):
    import html
    def esc(s):
        return html.escape("" if s is None else str(s))
    status = rep.get("status")
    if status == "generating" or not rep.get("content"):
        body = ('<div class="gen"><h2>Preparing the report for '
                f'{esc(month)}…</h2><p>This takes up to a minute. '
                '<a href="">refresh</a> shortly.</p></div>')
    else:
        gen = rep.get("generated_at")
        body = (f'<div class="meta">Generated {esc(str(gen)[:16])}'
                f' · <form method="post" action="/report/generate?month={esc(month)}"'
                ' style="display:inline"><button>Regenerate</button></form></div>'
                f'<article class="report">{_md_to_html(rep.get("content"))}</article>')
    picker = "".join(
        f'<a href="/report?month={esc(r[0])}"{" class=cur" if r[0]==month else ""}>{esc(r[0])}</a>'
        for r in months) or '<span class="muted">no reports yet</span>'
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kerger monthly report</title><style>
  :root{{--navy:#0B2942;--blue:#00B9F2;--ink:#0B1B26;--paper:#F4F7F9;--line:#E2E9EE;--muted:#5C6E7A}}
  *{{box-sizing:border-box}} body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--paper);color:var(--ink)}}
  header{{background:var(--navy);color:#fff;padding:18px 26px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;align-items:center}}
  header h1{{font-size:17px;margin:0}} header a{{color:#cddbe6;text-decoration:none;font-size:13px;margin-left:12px}}
  .months{{max-width:820px;margin:16px auto 0;padding:0 20px;display:flex;gap:8px;flex-wrap:wrap}}
  .months a{{font-size:12px;color:var(--muted);text-decoration:none;border:1px solid var(--line);border-radius:20px;padding:3px 11px;background:#fff}}
  .months a.cur{{background:var(--navy);color:#fff;border-color:var(--navy)}}
  main{{max-width:820px;margin:0 auto;padding:14px 20px 70px}}
  .meta{{font-size:12px;color:var(--muted);margin:10px 0 6px}} .meta button{{font-size:12px;cursor:pointer;border:1px solid var(--line);background:#fff;border-radius:6px;padding:3px 9px}}
  .report{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:26px 30px}}
  .report h2{{font-size:19px;margin:22px 0 8px;color:var(--navy)}} .report h2:first-child{{margin-top:0}}
  .report h3{{font-size:15px;margin:16px 0 6px}} .report p{{line-height:1.6;font-size:14.5px;margin:8px 0}}
  .report ul,.report ol{{padding-left:22px;line-height:1.6;font-size:14.5px}} .report li{{margin:4px 0}}
  .report code{{background:var(--paper);border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-size:13px}}
  .report strong{{color:var(--navy)}}
  .gen{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:40px;text-align:center;color:var(--muted)}}
</style><meta http-equiv="refresh" content="{'20' if status=='generating' else '99999'}"></head><body>
<header><h1>Kerger · monthly insights report</h1><nav><a href="/dashboard">← Live dashboard</a></nav></header>
<div class="months">{picker}</div>
<main>{body}</main></body></html>"""


def render_dashboard(d):
    import html
    def esc(s):
        return html.escape("" if s is None else str(s))

    chat = d.get("chat") or {}
    search = d.get("search") or {}
    days = d.get("days", 30)

    def tiles(items):
        cells = "".join(
            f'<div class="tile"><div class="n">{esc(v if v is not None else 0)}</div>'
            f'<div class="l">{esc(lbl)}</div></div>' for lbl, v in items)
        return f'<div class="tiles">{cells}</div>'

    def barlist(rows, unit=""):
        if not rows:
            return '<p class="empty">No data yet.</p>'
        mx = max((r[1] or 0) for r in rows) or 1
        out = []
        for r in rows:
            term, cnt = esc(r[0]), (r[1] or 0)
            extra = ""
            if len(r) > 2 and r[2] == 0:
                extra = ' <span class="zero">0 results</span>'
            out.append(
                f'<div class="row"><div class="lab">{term}{extra}</div>'
                f'<div class="track"><div class="fill" style="width:{cnt/mx*100:.1f}%"></div></div>'
                f'<div class="cnt">{cnt}{esc(unit)}</div></div>')
        return '<div class="bars">' + "".join(out) + "</div>"

    def daychart(rows):
        if not rows:
            return '<p class="empty">No data yet.</p>'
        mx = max((r[1] or 0) for r in rows) or 1
        cols = "".join(
            f'<div class="col" title="{esc(r[0])}: {r[1]}">'
            f'<div class="cbar" style="height:{max(3,(r[1] or 0)/mx*100):.0f}%"></div></div>'
            for r in rows)
        return f'<div class="days">{cols}</div>'

    questions = "".join(f"<li>{esc(q)}</li>" for q in d.get("recent_questions", [])) \
        or "<li class='empty'>No questions yet.</li>"

    nav = " · ".join(
        f'<a href="?days={n}"{" class=cur" if n==days else ""}>{n}d</a>'
        for n in (7, 30, 90))

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kerger insights</title><style>
  :root{{--navy:#0B2942;--blue:#00B9F2;--ink:#0B1B26;--paper:#F4F7F9;--line:#E2E9EE;--muted:#5C6E7A}}
  *{{box-sizing:border-box}} body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    background:var(--paper);color:var(--ink)}}
  header{{background:var(--navy);color:#fff;padding:18px 26px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}}
  header h1{{font-size:17px;margin:0;font-weight:700;letter-spacing:.02em}}
  header .sub{{font-size:12px;opacity:.7}} header nav a{{color:#cddbe6;text-decoration:none;font-size:13px;margin-left:6px}}
  header nav a.cur{{color:#fff;font-weight:700;border-bottom:2px solid var(--blue)}}
  main{{max-width:960px;margin:0 auto;padding:22px 20px 60px}}
  h2{{font-size:13px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin:30px 0 12px}}
  .tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
  .tile{{background:#fff;border:1px solid var(--line);border-radius:9px;padding:16px 18px}}
  .tile .n{{font-size:26px;font-weight:800;font-variant-numeric:tabular-nums}}
  .tile .l{{font-size:12px;color:var(--muted);margin-top:3px}}
  .panel{{background:#fff;border:1px solid var(--line);border-radius:9px;padding:18px 20px;margin-top:12px}}
  .bars .row{{display:grid;grid-template-columns:1fr 130px 46px;align-items:center;gap:12px;padding:4px 0}}
  .bars .lab{{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .bars .track{{background:#eef2f5;border-radius:5px;height:9px;overflow:hidden}}
  .bars .fill{{background:var(--blue);height:100%}}
  .bars .cnt{{text-align:right;font-variant-numeric:tabular-nums;font-size:12px;color:var(--muted)}}
  .zero{{color:#c26a00;font-size:11px;font-weight:600}}
  .days{{display:flex;align-items:flex-end;gap:3px;height:70px}}
  .days .col{{flex:1;display:flex;align-items:flex-end}} .days .cbar{{width:100%;background:var(--blue);border-radius:2px 2px 0 0;opacity:.85}}
  ul.q{{list-style:none;margin:0;padding:0}} ul.q li{{font-size:13px;padding:6px 0;border-bottom:1px solid #eef2f5}}
  .empty{{color:var(--muted);font-size:13px}} .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
  @media(max-width:640px){{.tiles{{grid-template-columns:1fr 1fr}}.grid2{{grid-template-columns:1fr}}.bars .row{{grid-template-columns:1fr 80px 40px}}}}
</style></head><body>
<header><div><h1>Kerger insights</h1><div class="sub">Advisor chat &amp; shop search · last {days} days</div></div>
  <nav><a href="/report" style="color:#fff;font-weight:700;margin-right:16px">Monthly AI report →</a>{nav}</nav></header>
<main>
  <h2>AI advisor</h2>
  {tiles([("Conversations", chat.get("conversations")), ("Messages", chat.get("messages")),
          ("Visitor questions", chat.get("questions")), ("Sent to sales", chat.get("leads"))])}
  <div class="panel"><h2 style="margin-top:0">Conversations per day</h2>{daychart(d.get("chat_by_day"))}</div>
  <div class="panel"><h2 style="margin-top:0">Recent visitor questions</h2><ul class="q">{questions}</ul></div>

  <h2>Shop &amp; chat search</h2>
  {tiles([("Searches", search.get("total")), ("No-result searches", search.get("zero")),
          ("Distinct top terms", len(d.get("top_terms", []))), ("", "")])}
  <div class="grid2">
    <div class="panel"><h2 style="margin-top:0">Top search terms</h2>{barlist(d.get("top_terms"))}</div>
    <div class="panel"><h2 style="margin-top:0">Searches with no results <span class="zero">demand gaps</span></h2>{barlist(d.get("zero_terms"))}</div>
  </div>
  <div class="panel"><h2 style="margin-top:0">Searches per day</h2>{daychart(d.get("search_by_day"))}</div>
</main></body></html>"""


def _scheduler():
    """Wake periodically and generate last month's report when it's due.
    claim_report() makes this safe across multiple gunicorn workers."""
    while True:
        try:
            _autogenerate_due()
        except Exception:
            pass
        time.sleep(6 * 3600)


if store.enabled():
    Thread(target=_scheduler, daemon=True).start()


if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ERROR: ANTHROPIC_API_KEY not set (in .env).")
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
