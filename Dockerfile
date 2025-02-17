FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app/

# Create a script to initialize the database and start the server
COPY scripts/start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]