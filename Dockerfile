FROM uselagoon/python-3.12:latest@sha256:ea451aa4106d48374ac976832a216974a3da5f5885609970370675a5c4c13c95

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
