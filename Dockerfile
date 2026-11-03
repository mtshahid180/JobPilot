# syntax=docker/dockerfile:1

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies from the project metadata, then copy the package source.
# Keeping this layer order makes rebuilds faster when only app code changes.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

# Run as an unprivileged user. UID/GID 1000 keeps bind-mounted ./data and
# ./traces writable on typical Linux hosts; Docker Desktop handles this on
# Windows and macOS automatically.
RUN useradd --create-home --uid 1000 --user-group jobpilot \
    && mkdir -p /app/data /app/traces \
    && chown -R jobpilot:jobpilot /app

USER jobpilot

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=3)"

CMD ["jobpilot", "serve", "--host", "0.0.0.0", "--port", "5000"]
