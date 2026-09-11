"""
Watch the SFTP incoming/ folder and import each dropped stock/price file into
Odoo (tools/import_stock_file.py --apply), then archive it.

A file is only imported once it's fully uploaded — detected by its size being
stable across two checks — so a half-transferred file is never processed.
Odoo credentials come from the container environment (Railway variables).
"""

import datetime
import os
import shutil
import subprocess
import time

INCOMING = "/data/upload/incoming"
PROCESSED = "/data/upload/processed"
IMPORTER = "/app/tools/import_stock_file.py"
EXTS = (".csv", ".xls", ".xlsx")
STABLE_SECONDS = 15
POLL_SECONDS = 30


def _stable(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    time.sleep(STABLE_SECONDS)
    try:
        return os.path.getsize(path) == size and size > 0
    except OSError:
        return False


def _log(*a):
    print(f"[{datetime.datetime.utcnow().isoformat(timespec='seconds')}Z]", *a, flush=True)


def process(path, name):
    _log("importing", name)
    r = subprocess.run(["python3", IMPORTER, "--file", path, "--apply"],
                       capture_output=True, text=True)
    if r.stdout:
        _log("out:", r.stdout.strip().replace("\n", " | "))
    if r.returncode != 0:
        _log("IMPORT FAILED:", (r.stderr or "")[-800:])
    ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(PROCESSED, f"{ts}-{name}")
    try:
        shutil.move(path, dest)
    except OSError:
        try:
            os.remove(path)
        except OSError:
            pass


def main():
    _log("poller started; watching", INCOMING)
    while True:
        try:
            for name in sorted(os.listdir(INCOMING)):
                if not name.lower().endswith(EXTS):
                    continue
                path = os.path.join(INCOMING, name)
                if os.path.isfile(path) and _stable(path):
                    process(path, name)
        except Exception as e:  # never let the loop die
            _log("poller error:", e)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
