FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV BRAGGRAPHS_DATA_DIR=/data \
    BRAGGRAPHS_CONFIG=/app/config.yml \
    PYTHONUNBUFFERED=1

VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=4)"

# Exactly one worker: the scheduler, response cache, and rate limiter are
# in-process. Threads keep graph serving responsive during fetches.
CMD ["gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:8000", "app:create_app(start_scheduler=True)"]
