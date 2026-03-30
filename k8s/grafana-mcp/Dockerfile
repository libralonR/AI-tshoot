# Dockerfile para Grafana MCP Server
# Build: docker build -t grafana-mcp-server:latest -f k8s/grafana-mcp/Dockerfile .
# (executar da raiz do projeto)
FROM python:3.11-slim

WORKDIR /app

COPY k8s/grafana-mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp-servers/grafana_v2.py .

RUN useradd -m -u 1000 mcp && chown -R mcp:mcp /app
USER mcp

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import sys; sys.exit(0)"

CMD ["python", "grafana_v2.py"]
