FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY incidents_pg.py .

RUN useradd -m -u 1000 mcp && chown -R mcp:mcp /app
USER mcp

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import sys; sys.exit(0)"

CMD ["python", "incidents_pg.py"]
