"""
Log shop search terms to the advisor backend for the insights dashboard.

Injects a small script site-wide (website.custom_code_footer) that, on a shop
search results page (URL has ?search=), reports the term, the number of results
shown, and whether the visitor is logged in — via navigator.sendBeacon to the
Railway backend's /search-log (fire-and-forget, no CORS pre-flight, no effect on
page load). Idempotent, marker-wrapped; --remove takes it back down.

Usage:
  python3 tools/push_search_logger.py --url https://APP.up.railway.app --apply
  python3 tools/push_search_logger.py --remove --apply
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

START = "<!-- == kerger-searchlog start == -->"
END = "<!-- == kerger-searchlog end == -->"
FIELD = "custom_code_footer"


def block(url):
    url = url.rstrip("/")
    return f"""{START}
<script>
(function(){{
  try{{
    var path=location.pathname;
    var onShop=path.indexOf('/shop')===0;               // shop search box
    var onSite=path.indexOf('/website/search')===0;      // header (site-wide) search box
    if(!onShop && !onSite) return;
    var term=new URLSearchParams(location.search).get('search');
    if(!term) return;
    // result count is reliable on the shop grid (.oe_product_cart); on the
    // site-wide results page leave it unknown so it isn't miscounted as 0.
    var results=onShop ? document.querySelectorAll('.oe_product_cart').length : null;
    var loggedIn=!!document.querySelector('a[href*="/web/session/logout"]');
    var payload=JSON.stringify({{term:term, results:results, logged_in:loggedIn,
                                 source:(onShop?'shop':'site')}});
    var url="{url}/search-log";
    if(navigator.sendBeacon){{navigator.sendBeacon(url, payload);}}
    else{{fetch(url,{{method:'POST',body:payload,keepalive:true}});}}
  }}catch(e){{}}
}})();
</script>
{END}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="Railway backend URL")
    ap.add_argument("--remove", action="store_true")
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
    stripped = re.sub(re.escape(START) + r".*?" + re.escape(END), "",
                      original, flags=re.DOTALL).strip()
    new = stripped if args.remove else \
        ((stripped + "\n" + block(args.url)).strip() if stripped else block(args.url))

    if args.dry_run:
        print(new)
        return
    c.write("website", [site["id"]], {FIELD: new})
    print(f"search logger {'removed' if args.remove else 'installed'} on website id {site['id']}")


if __name__ == "__main__":
    main()
