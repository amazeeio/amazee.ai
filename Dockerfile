FROM uselagoon/python-3.11:latest

RUN apk add bash --no-cache

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app .

# Script to initialize the database and start the server
RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]