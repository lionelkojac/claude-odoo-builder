"""
Add a "Request an account" link to the website login page.

Signup is invitation-only (sales grants portal access), so the public "Sign up"
link is gone. This adds a link, in the same spot, that points prospects at the
contact form (/contactus) to request access — sales then grants it.

Inherits web.login and inserts after the login button, using the same anchor
Odoo's own auth_signup.login view uses (//div[hasclass('oe_login_buttons')]/
button), so it's a safe, supported insertion point. Reversible with --remove.

Usage:
  python3 tools/push_request_access_link.py --apply
  python3 tools/push_request_access_link.py --remove
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from odoo_client import OdooClient

KEY = "kerger.login_request_access"
ARCH = """<data>
  <xpath expr="//div[hasclass('oe_login_buttons')]/button" position="after">
    <a class="btn btn-link btn-sm mt-2 d-block" href="/contactus">Need an account? Request access</a>
  </xpath>
</data>"""


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--apply", action="store_true")
    g.add_argument("--remove", action="store_true")
    args = ap.parse_args()

    c = OdooClient(); c.authenticate()
    old = c._execute_kw("ir.ui.view", "search", [[["key", "=", KEY]]], {})
    if old:
        c._execute_kw("ir.ui.view", "unlink", [old], {})
        print(f"removed {len(old)} existing link view(s)")
    if args.remove:
        print("request-access link removed.")
        return

    base = c._execute_kw("ir.ui.view", "search", [[["key", "=", "web.login"]]], {"limit": 1})
    if not base:
        sys.exit("ERROR: web.login view not found")
    vid = c._execute_kw("ir.ui.view", "create", [{
        "name": "Kerger request access link", "key": KEY, "type": "qweb",
        "inherit_id": base[0], "mode": "extension", "active": True,
        "arch_db": ARCH}], {})
    print(f"created {KEY} (id {vid}) — verify /web/login renders.")


if __name__ == "__main__":
    main()
