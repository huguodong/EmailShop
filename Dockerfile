# ponytail: stdlib-only app, so no pip layer — just copy the script and run.
FROM python:3.12-slim

WORKDIR /app
COPY mail_bridge_server.py ./

# Data (db, config, logs) lives on a mounted volume at /data.
ENV MAIL_BRIDGE_HOST=0.0.0.0 \
    MAIL_BRIDGE_PORT=8880 \
    MAIL_BRIDGE_DB=/data/mail_bridge.sqlite3 \
    MAIL_BRIDGE_CONFIG=/data/config.json \
    MAIL_BRIDGE_LOG_DIR=/data/logs

# Run as a non-root user. ponytail: if a bind-mounted ./data isn't writable by
# this uid on Linux, `chown -R 10001:10001 ./data` on the host (Docker Desktop
# on Win/Mac maps this automatically).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /data && chown -R appuser:appuser /data /app
USER appuser

EXPOSE 8880
VOLUME ["/data"]

# Stdlib healthcheck (no curl in slim) hitting the app's /health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8880/health', timeout=4).status==200 else 1)"

CMD ["python", "mail_bridge_server.py"]
