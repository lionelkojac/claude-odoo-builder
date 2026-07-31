# Kerridge → Railway → Odoo (SFTP stock/price pipeline)

The Kerridge ERP uploads `PRODUCT_LIST.CSV` by **SFTP** to this small service on
Railway. A poller detects the finished upload and runs
`tools/import_stock_file.py` to push **sales price + quantity on hand + stock
badge** into Odoo. Everything stays on Railway — no VPS, no third party.

```
Kerridge ERP ──SFTP──▶  this service (sshd + poller)  ──Odoo RPC──▶  Odoo
 PRODUCT_LIST.CSV        /data/upload/incoming            price · qty · badge
```

## Deploy on Railway (a SECOND service in your existing project)

1. **New → Deploy from GitHub repo** → same repo → this becomes a second service
   alongside the chatbot.
2. In that service's **Settings**:
   - **Config → Railway Config File**: `sftp/railway.json`  ← **the one setting that
     matters.** The repo root `railway.json` is the *chatbot's* config — it forces a
     Nixpacks build, a `gunicorn …` start command, and a `/health` healthcheck. An
     SFTP container has none of those, so inheriting it fails the deploy twice over
     (*"executable `gunicorn` could not be found"*, then a healthcheck timeout on a
     port with no web server). Pointing this service at its own `sftp/railway.json`
     makes it build from `sftp/Dockerfile`, start with `/app/start.sh`, and skip the
     healthcheck — no other Build/Deploy overrides needed.
   - **Volumes → New Volume**, mount path **`/data`** (persists host keys + files).
   - **Networking → TCP Proxy → Add** on target port **`2222`**. Railway returns a
     host + external port, e.g. `containers-xxx.railway.app : 43210`. **That
     host and port are what the ERP connects to.**
3. **Variables** (this service):
   - `SFTP_USER` (e.g. `kerridge`), `SFTP_PASSWORD` (a strong password)
   - `ODOO_URL=https://kerger.odoo.com`, `ODOO_DB=kerger`, `ODOO_USER`, `ODOO_PASSWORD`
   - `ODOO_STOCK_LOCATION_ID` (the on-hand location, e.g. `14`)
4. Deploy. Open the **deploy logs** — on boot it prints the **host-key
   fingerprint** (needed for the ERP) and the SFTP user/port.

## Point Kerridge at it (the "Copy files via FTP" task)
- **SFTP** (not plain FTP).
- **URL / host**: the TCP-proxy host. **Port**: the TCP-proxy port (NOT 22).
  *If the ERP's URL field can't take a port, enter `host:port`, or check for a
  separate port field — this is the main thing the connection test verifies.*
- **User**: `SFTP_USER`   **Access code**: `SFTP_PASSWORD`
- **Fingerprint of host**: the fingerprint from the deploy logs.
- **File(s)**: `PRODUCT_LIST.CSV`   **Upload to server**, After transfer: **Delete**.

## Test the connection
1. Run the ERP task once (or manually SFTP a small CSV to `/incoming`).
2. Watch the service logs: `poller started …` then `importing PRODUCT_LIST.CSV`
   and an `out: …` summary. The file moves to `/data/upload/processed`.
3. Check Odoo (price / on-hand / badge on a product from the file).

## CSV format
Columns detected by header (same as the manual STOCK export):
`Product | Sales Price | Max Stock | Min Stock | Available stock`.
If Kerridge's `PRODUCT_LIST.CSV` differs, send one sample and the importer's
column mapping is a one-line change.

## Notes
- Only **storable** products get an on-hand update; price + badge apply to all.
- The importer is idempotent — re-running a file just re-syncs.
- Security: the user is chrooted and SFTP-only (no shell); use a strong password.
