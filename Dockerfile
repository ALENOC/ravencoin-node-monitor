FROM python:3.13-slim

RUN useradd --system --create-home --uid 10001 monitor
WORKDIR /app
COPY app/ /app/

USER monitor
EXPOSE 8899

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8899/api/status', timeout=4)" || exit 1

ENTRYPOINT ["python3", "server.py"]
