"""
Analytics store for the Kerger advisor + shop search (optional Postgres).

Everything here is best-effort and OFF by default: with no DATABASE_URL set,
`enabled()` is False and every call is a silent no-op, so the app runs exactly
as before. Add a Postgres database on Railway (it injects DATABASE_URL) and this
starts logging:
  - chat_messages : one row per chat turn (session, role, text, model)
  - searches      : one row per shop/chat search (term, result count, logged-in)

Writes are fire-and-forget on a background thread so they never slow a request.
Rows older than ANALYTICS_RETENTION_DAYS (default 90) are purged.

Privacy: session ids are random (not identities); transcripts may still contain
what a visitor typed, so this is personal data — keep the retention window and
tell visitors (see the widget privacy line).
"""

import os
import threading
import datetime

import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")
RETENTION_DAYS = int(os.getenv("ANALYTICS_RETENTION_DAYS", "90"))

_init_done = False
_last_purge = 0.0
_lock = threading.Lock()


def enabled():
    return bool(DATABASE_URL)


def _conn():
    return psycopg2.connect(DATABASE_URL, connect_timeout=8)


def init():
    """Create tables/indexes once. Safe to call repeatedly."""
    global _init_done
    if not enabled() or _init_done:
        return
    with _lock:
        if _init_done:
            return
        try:
            with _conn() as c, c.cursor() as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS chat_messages(
                    id BIGSERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
                    session_id TEXT, role TEXT, content TEXT, model TEXT)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS searches(
                    id BIGSERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
                    term TEXT, results INTEGER, logged_in BOOLEAN,
                    source TEXT DEFAULT 'shop')""")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_ts ON chat_messages(ts)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_search_ts ON searches(ts)")
                cur.execute("""CREATE TABLE IF NOT EXISTS reports(
                    month TEXT PRIMARY KEY, status TEXT,
                    content TEXT, generated_at TIMESTAMPTZ DEFAULT now())""")
            _init_done = True
        except Exception:
            pass


def _purge_if_due():
    global _last_purge
    import time
    now = time.time()
    if now - _last_purge < 3600:      # at most hourly
        return
    _last_purge = now
    try:
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=RETENTION_DAYS)
        with _conn() as c, c.cursor() as cur:
            cur.execute("DELETE FROM chat_messages WHERE ts < %s", (cutoff,))
            cur.execute("DELETE FROM searches WHERE ts < %s", (cutoff,))
    except Exception:
        pass


def _bg(fn):
    if not enabled():
        return
    threading.Thread(target=fn, daemon=True).start()


def log_chat(session_id, role, content, model=None):
    def run():
        init(); _purge_if_due()
        try:
            with _conn() as c, c.cursor() as cur:
                cur.execute(
                    "INSERT INTO chat_messages(session_id,role,content,model) VALUES(%s,%s,%s,%s)",
                    (session_id, role, (content or "")[:8000], model))
        except Exception:
            pass
    _bg(run)


def log_search(term, results=None, logged_in=None, source="shop"):
    term = (term or "").strip()
    if not term:
        return
    def run():
        init(); _purge_if_due()
        try:
            with _conn() as c, c.cursor() as cur:
                cur.execute(
                    "INSERT INTO searches(term,results,logged_in,source) VALUES(%s,%s,%s,%s)",
                    (term[:500], results, logged_in, source))
        except Exception:
            pass
    _bg(run)


# ---------------- dashboard reads (synchronous) ----------------

def _rows(sql, params=()):
    if not enabled():
        return []
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except Exception:
        return []


def dashboard_data(days=30):
    """Aggregates for the dashboard, over the last `days` days."""
    since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    d = {"days": days, "enabled": enabled()}

    # chat totals
    r = _rows("""SELECT count(DISTINCT session_id),
                        count(*) FILTER (WHERE role IN ('user','assistant')),
                        count(*) FILTER (WHERE role='user'),
                        count(*) FILTER (WHERE role='lead')
                 FROM chat_messages WHERE ts >= %s""", (since,))
    d["chat"] = dict(zip(("conversations", "messages", "questions", "leads"),
                         r[0])) if r else {}
    # recent visitor questions
    d["recent_questions"] = [x[0] for x in _rows(
        """SELECT content FROM chat_messages WHERE role='user' AND ts >= %s
           ORDER BY ts DESC LIMIT 40""", (since,))]
    # chat volume by day
    d["chat_by_day"] = _rows(
        """SELECT to_char(date_trunc('day',ts),'YYYY-MM-DD'), count(DISTINCT session_id)
           FROM chat_messages WHERE ts >= %s GROUP BY 1 ORDER BY 1""", (since,))

    # search totals
    r = _rows("""SELECT count(*), count(*) FILTER (WHERE results=0)
                 FROM searches WHERE ts >= %s""", (since,))
    d["search"] = dict(zip(("total", "zero"), r[0])) if r else {}
    # top terms
    d["top_terms"] = _rows(
        """SELECT lower(term), count(*), min(coalesce(results,0))
           FROM searches WHERE ts >= %s GROUP BY 1 ORDER BY 2 DESC LIMIT 25""", (since,))
    # zero-result terms (the demand gaps)
    d["zero_terms"] = _rows(
        """SELECT lower(term), count(*) FROM searches
           WHERE ts >= %s AND results=0 GROUP BY 1 ORDER BY 2 DESC LIMIT 25""", (since,))
    # search volume by day
    d["search_by_day"] = _rows(
        """SELECT to_char(date_trunc('day',ts),'YYYY-MM-DD'), count(*)
           FROM searches WHERE ts >= %s GROUP BY 1 ORDER BY 1""", (since,))
    return d


# ---------------- monthly AI report ----------------

def _month_bounds(year, month):
    start = datetime.datetime(year, month, 1)
    nxt = datetime.datetime(year + (month == 12), (month % 12) + 1, 1)
    return start, nxt


def report_dataset(year, month, question_sample=80):
    """Everything the AI report needs for one calendar month, plus the previous
    month's headline totals for trend."""
    start, nxt = _month_bounds(year, month)
    pstart, _ = _month_bounds(year - (month == 1), (month - 2) % 12 + 1)

    def totals(a, b):
        s = _rows("""SELECT count(*), count(*) FILTER (WHERE results=0)
                     FROM searches WHERE ts >= %s AND ts < %s""", (a, b))
        c = _rows("""SELECT count(DISTINCT session_id),
                            count(*) FILTER (WHERE role='user'),
                            count(*) FILTER (WHERE role='lead')
                     FROM chat_messages WHERE ts >= %s AND ts < %s""", (a, b))
        return {"searches": (s[0][0] if s else 0), "zero": (s[0][1] if s else 0),
                "conversations": (c[0][0] if c else 0),
                "questions": (c[0][1] if c else 0), "leads": (c[0][2] if c else 0)}

    d = {"period": f"{year}-{month:02d}",
         "this_month": totals(start, nxt), "prev_month": totals(pstart, start)}
    d["top_terms"] = _rows(
        """SELECT lower(term), count(*), min(coalesce(results,-1))
           FROM searches WHERE ts >= %s AND ts < %s GROUP BY 1
           ORDER BY 2 DESC LIMIT 40""", (start, nxt))
    d["zero_terms"] = _rows(
        """SELECT lower(term), count(*) FROM searches
           WHERE ts >= %s AND ts < %s AND results=0 GROUP BY 1
           ORDER BY 2 DESC LIMIT 40""", (start, nxt))
    d["questions"] = [x[0] for x in _rows(
        """SELECT content FROM chat_messages WHERE role='user'
           AND ts >= %s AND ts < %s ORDER BY ts DESC LIMIT %s""",
        (start, nxt, question_sample))]
    return d


def claim_report(month):
    """Atomically claim generation of `month` (YYYY-MM). True if we won the race
    (row newly inserted); False if it already exists — avoids duplicate work
    across gunicorn workers."""
    if not enabled():
        return False
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute("INSERT INTO reports(month,status) VALUES(%s,'generating') "
                        "ON CONFLICT (month) DO NOTHING", (month,))
            return cur.rowcount == 1
    except Exception:
        return False


def save_report(month, content):
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute("UPDATE reports SET content=%s, status='done', "
                        "generated_at=now() WHERE month=%s", (content, month))
    except Exception:
        pass


def delete_report(month):
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute("DELETE FROM reports WHERE month=%s", (month,))
    except Exception:
        pass


def get_report(month):
    r = _rows("SELECT month, content, status, generated_at FROM reports WHERE month=%s",
              (month,))
    return dict(zip(("month", "content", "status", "generated_at"), r[0])) if r else None


def list_reports():
    return _rows("SELECT month, status, generated_at FROM reports ORDER BY month DESC")
