FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 1) Dependencies: this layer is cached until pyproject.toml or the lockfile changes.
COPY pyproject.toml requirements.lock ./
RUN mkdir -p src/whymoved \
    && touch src/whymoved/__init__.py \
    && pip install -c requirements.lock .

# 2) Application code: only this layer rebuilds when you edit code.
COPY src ./src
RUN pip install --no-deps --force-reinstall -c requirements.lock .

# The API downloads the nightly snapshot into /app/data, so that folder must be writable.
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown app /app/data
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'))"

# Hosts like Render set PORT; locally it falls back to 8000.
CMD ["sh", "-c", "exec uvicorn whymoved.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
