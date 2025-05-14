FROM uselagoon/python-3.12:latest

RUN apk add bash --no-cache

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app/
COPY scripts scripts/

# Copy Lagon environment variables
COPY .lagoon.env .

# Script to initialize the database and start the server
RUN mkdir -p /app/logs && chmod 666 /app/logs
COPY backend-start.sh .
RUN chmod +x /app/backend-start.sh

CMD ["/app/backend-start.sh"]