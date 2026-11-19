# Python image shared by the api, ingest and pipeline (decode + store) services.
# Match the Python version used in .venv and CI.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so rebuilds are fast when only code changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY api.py ./
COPY ingest/ ingest/
COPY decode/ decode/
COPY store/ store/
COPY track/ track/
COPY mapping/ mapping/
COPY tests/ tests/
COPY docker/pipeline.sh /usr/local/bin/pipeline.sh

# Run as a non-root user. /app/data is created here so a fresh named volume
# mounted on it inherits this ownership.
RUN chmod +x /usr/local/bin/pipeline.sh \
 && useradd --create-home --uid 1000 app \
 && mkdir -p /app/data \
 && chown -R app:app /app
USER app

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
