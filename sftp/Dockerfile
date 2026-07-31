# SFTP receiver for the Kerridge -> Odoo stock/price pipeline.
# The ERP uploads PRODUCT_LIST.CSV via SFTP into this container; a poller runs
# tools/import_stock_file.py to push price + on-hand + badge into Odoo.
#
# Build context is the REPO ROOT (so it can COPY tools/). On Railway set the
# service's Dockerfile path to sftp/Dockerfile and attach a volume at /data.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-server openssh-client \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir requests python-dotenv xlrd

WORKDIR /app
COPY tools/ /app/tools/
COPY sftp/start.sh sftp/poller.py /app/
RUN chmod +x /app/start.sh

# SFTP listens here inside the container; point Railway's TCP proxy at this port.
ENV SFTP_PORT=2222
EXPOSE 2222

# ENTRYPOINT (not CMD): Railway's start command overrides a Docker image's CMD
# but never its ENTRYPOINT. So even if this service inherits the chatbot's root
# railway.json/Procfile "gunicorn …" start command, that string is passed as
# harmless *arguments* to start.sh (which ignores positional args) instead of
# replacing it — the "executable gunicorn could not be found" failure can no
# longer happen no matter how the Railway service is configured.
ENTRYPOINT ["/app/start.sh"]
