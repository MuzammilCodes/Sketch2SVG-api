# ── base image ──────────────────────────────────────────────────
FROM python:3.11-slim

# ── install potrace (the native binary) + build deps ────────────
RUN apt-get update && apt-get install -y \
    potrace \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── set workdir ──────────────────────────────────────────────────
WORKDIR /app

# ── install python deps ──────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── copy source ──────────────────────────────────────────────────
COPY app.py .

# ── expose port ──────────────────────────────────────────────────
EXPOSE 5000

# ── start with gunicorn (production WSGI server) ─────────────────
CMD gunicorn app:app \
    --bind 0.0.0.0:${PORT:-5000} \
    --workers 2 \
    --timeout 60
