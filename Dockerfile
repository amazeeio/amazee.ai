# ---------- base ----------
# Shared foundation: Python deps, app code, and scripts.
# Both `backend` (the FastAPI service) and `cli` extend this
# so we install requirements.txt exactly once.
FROM uselagoon/python-3.12:latest@sha256:5ab457220705f7b4c072ee746b5920779a385a70175e0471b9a263c840ff1070 AS base

RUN apk add --no-cache bash curl

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app/
COPY scripts scripts/

ENV PYTHONPATH=/app

# ---------- backend ----------
# FastAPI service. This is the default target so existing
# `docker build .` / Lagoon builds keep working unchanged.
FROM base AS backend

# Copy Lagoon environment variables
COPY .lagoon.env .

# Script to initialize the database and start the server
RUN mkdir -p /app/logs && \
    chown -R 1000:1000 /app/logs && \
    chmod 775 /app/logs

COPY backend-start.sh .
RUN chmod +x /app/backend-start.sh

CMD ["/app/backend-start.sh"]

# ---------- cli ----------
# CLI / migration / DB-restore pod. Adds postgres client tooling
# (matched to pgvector/pgvector:pg16 server) and the usual CLI niceties.
FROM base AS cli

ENV LAGOON=cli

RUN apk add --no-cache \
        coreutils \
        findutils \
        git \
        gzip \
        openssh-client \
        openssh-sftp-server \
        postgresql16-client \
        procps \
        unzip \
    && rm -rf /var/cache/apk/* \
    && ln -s /usr/lib/ssh/sftp-server /usr/local/bin/sftp-server \
    && mkdir -p /home/.ssh \
    && fix-permissions /home/

# Idle shells get killed (matches php-cli/node-cli convention).
# Guarded because python-3.12 base does not always ship 80-shell-timeout.sh.
RUN echo "[ -f /lagoon/entrypoints/80-shell-timeout.sh ] && source /lagoon/entrypoints/80-shell-timeout.sh" >> /home/.bashrc

CMD ["/bin/docker-sleep"]

