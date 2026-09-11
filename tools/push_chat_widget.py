"""
Inject the Kerger product-advisor chat launcher site-wide.

Adds a floating round button (bottom-right, brand blue) to every website page;
clicking it toggles an iframe panel that loads the advisor widget served by the
Railway backend. Idempotent: the block is wrapped in HTML comment markers and
replaced on re-run. Written into website.custom_code_footer (rendered just
before </body>), so nothing is needed on the Odoo page templates themselves.

Usage:
  python3 tools/push_chat_widget.py --url https://APP.up.railway.app --dry-run
  python3 tools/push_chat_widget.py --url https://APP.up.railway.app --apply
  python3 tools/push_chat_widget.py --remove --apply        # take it back down
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

START = "<!-- == kerger-advisor start == -->"
END = "<!-- == kerger-advisor end == -->"
FIELD = "custom_code_footer"


def block(url):
    url = url.rstrip("/")
    return f"""{START}
<style>
  #kerger-advisor-btn{{position:fixed;bottom:22px;right:22px;width:60px;height:60px;
    border-radius:50%;background:#00B9F2;border:0;cursor:pointer;z-index:2147483000;
    box-shadow:0 8px 24px rgba(11,41,66,.28);display:flex;align-items:center;
    justify-content:center;transition:transform .15s ease}}
  #kerger-advisor-btn:hover{{transform:scale(1.06)}}
  #kerger-advisor-btn svg{{width:28px;height:28px;fill:#fff}}
  #kerger-advisor-panel{{position:fixed;bottom:94px;right:22px;width:400px;
    height:600px;max-height:calc(100vh - 120px);border:0;border-radius:14px;
    box-shadow:0 16px 48px rgba(11,41,66,.30);z-index:2147483000;background:#fff;
    display:none;overflow:hidden}}
  #kerger-advisor-panel.open{{display:block}}
  @media (max-width:480px){{
    #kerger-advisor-panel{{width:calc(100vw - 24px);right:12px;bottom:88px;
      height:calc(100vh - 110px)}}
    #kerger-advisor-btn{{bottom:16px;right:16px}}
  }}
</style>
<button id="kerger-advisor-btn" aria-label="Product advisor">
  <svg viewBox="0 0 24 24"><path d="M12 3C6.5 3 2 6.8 2 11.5c0 2.4 1.2 4.6 3.1 6.1L4 21.5l4.2-2.1c1.2.3 2.5.5 3.8.5 5.5 0 10-3.8 10-8.4S17.5 3 12 3z"/></svg>
</button>
<iframe id="kerger-advisor-panel" title="Kerger product advisor" src="about:blank"></iframe>
<script>
(function(){{
  var URL="{url}/";
  var btn=document.getElementById('kerger-advisor-btn');
  var panel=document.getElementById('kerger-advisor-panel');
  if(!btn||!panel) return;
  function open(){{
    panel.classList.add('open');
    if(panel.getAttribute('src')==='about:blank') panel.setAttribute('src',URL);
  }}
  btn.addEventListener('click',function(){{
    // once the visitor interacts, don't auto-pop again this session
    try{{sessionStorage.setItem('kerger_advisor_seen','1');}}catch(e){{}}
    if(panel.classList.contains('open')) panel.classList.remove('open'); else open();
  }});
  // auto-open once per browser session, 10s after load
  setTimeout(function(){{
    try{{ if(sessionStorage.getItem('kerger_advisor_seen')) return; }}catch(e){{}}
    if(!panel.classList.contains('open')){{
      open();
      try{{sessionStorage.setItem('kerger_advisor_seen','1');}}catch(e){{}}
    }}
  }}, 10000);
}})();
</script>
{END}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="Railway backend URL (e.g. https://app.up.railway.app)")
    ap.add_argument("--remove", action="store_true", help="Remove the widget block")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.remove and not args.url:
        ap.error("--url is required unless --remove")

    c = OdooClient()
    sites = c.search_read("website", [], ["id", "name", FIELD])
    if not sites:
        sys.exit("ERROR: no website record found.")
    site = sites[0]
    original = site.get(FIELD) or ""

    # strip any existing block first (idempotent)
    pattern = re.escape(START) + r".*?" + re.escape(END)
    stripped = re.sub(pattern, "", original, flags=re.DOTALL).strip()

    if args.remove:
        new = stripped
    else:
        new = (stripped + "\n" + block(args.url)).strip() if stripped else block(args.url)

    if args.dry_run:
        print(f"--- DRY RUN on website '{site['name']}' (id {site['id']}), field {FIELD} ---")
        print(new)
        print(f"--- char delta: {len(new) - len(original):+d} ---")
        return

    c.write("website", [site["id"]], {FIELD: new})
    verb = "removed" if args.remove else "installed"
    print(f"OK: chat widget {verb} on website id {site['id']} "
          f"({len(new)} chars, {len(new) - len(original):+d}).")


if __name__ == "__main__":
    main()
