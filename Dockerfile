FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyrowatch ./pyrowatch
COPY web ./web

ENV HOST=0.0.0.0 \
    PORT=8000 \
    DB_PATH=/app/data/pyrowatch.db \
    ALERT_DESKTOP=false

VOLUME ["/app/data"]
EXPOSE 8000

CMD ["python", "-m", "pyrowatch"]
