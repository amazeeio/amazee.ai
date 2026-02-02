FROM uselagoon/python-3.12:latest@sha256:7b812e983897599b1e2f928aeca6b940cbd8071148666e4c6bb1ba5a1f886c9d

RUN apk add bash --no-cache
RUN apk add curl --no-cache

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app/
COPY scripts scripts/

# Copy Lagoon environment variables
COPY .lagoon.env .

# Script to initialize the database and start the server
RUN mkdir -p /app/logs && \
    chown -R 1000:1000 /app/logs && \
    chmod 775 /app/logs
COPY backend-start.sh .
RUN chmod +x /app/backend-start.sh

CMD ["/app/backend-start.sh"]
