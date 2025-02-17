FROM uselagoon/python-3.11:latest

RUN apk add bash --no-cache

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app/

# Script to initialize the database and start the server
COPY backend-start.sh .
RUN chmod +x /app/backend-start.sh

CMD ["/app/backend-start.sh"]