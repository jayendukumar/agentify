# US9.7: a simple built container (not hot-reload dev mode -- iterate
# locally via .venv/uvicorn --reload as before; this is for one-command
# full-stack bring-up). sentence-transformers pulls in torch, so this
# image is large (~2-3GB) and the embedding model downloads from
# HuggingFace on first use inside the container -- same characteristic
# the non-Docker local dev workflow already has, not new here.
FROM python:3.11-slim

WORKDIR /app

# psycopg[binary] ships prebuilt wheels, but keep build-essential for any
# source builds pip falls back to on an architecture without a wheel.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir .

EXPOSE 8000

# Runs pending migrations before starting -- the db service's own data
# lives in a named volume that outlives container recreation, so this
# must be idempotent (Alembic's upgrade already is).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
