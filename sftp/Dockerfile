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
CMD ["/app/start.sh"]
