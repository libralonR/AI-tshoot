# Dockerfile para Incidents PostgreSQL MCP Server
# Build: docker build -t incidents-pg-mcp-server:latest -f k8s/incidents-pg-mcp/Dockerfile .
# (executar da raiz do projeto)
FROM python:3.11-slim

WORKDIR /app

COPY k8s/incidents-pg-mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp-servers/incidents_pg.py .

RUN useradd -m -u 1000 mcp && chown -R mcp:mcp /app
USER mcp

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import sys; sys.exit(0)"

CMD ["python", "incidents_pg.py"]
