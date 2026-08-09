# Multi-stage. Runtime is slim, non-root, read-only-friendly, with no secrets.
FROM python:3.12-slim AS builder
WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml ./
COPY app ./app
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -q . \
 && /opt/venv/bin/pip install --no-cache-dir -q ".[postgres,aws,parsers]"

FROM python:3.12-slim AS runtime
# Non-root from the start; uid is fixed so k8s runAsUser can match.
RUN groupadd -g 10001 app && useradd -u 10001 -g app -m -s /usr/sbin/nologin app
WORKDIR /srv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app ./app
USER 10001:10001
EXPOSE 8000
# No secrets in ENV, ARG or any layer — they arrive at runtime from Secrets Manager.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
