# Dockerfile para Logs Parquet MCP Server
# Build: docker build -t logs-mcp-server:latest -f k8s/logs-mcp/Dockerfile .
# (executar a partir da raiz do projeto)
FROM python:3.11-slim

WORKDIR /app

# Build deps mínimas (DuckDB já vem como wheel; httpfs é dinâmica)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY k8s/logs-mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pré-instalar extensão httpfs do DuckDB durante o build.
# Isso evita que o container precise baixar em runtime (onde pode dar 403
# por rate limit, network policy, ou falta de egress).
# Instalamos em /app/.duckdb_extensions/ que será acessível pelo user mcp.
RUN python -c "\
import duckdb; \
conn = duckdb.connect(); \
conn.execute(\"SET extension_directory='/app/.duckdb_extensions'\"); \
conn.execute('INSTALL httpfs'); \
conn.close(); \
print('httpfs pre-installed in /app/.duckdb_extensions')"

COPY mcp-servers/logs_parquet/ ./logs_parquet/

RUN useradd -m -u 1000 mcp && chown -R mcp:mcp /app
USER mcp

# Apontar DuckDB para o diretório de extensões pré-instalado
ENV DUCKDB_EXTENSION_DIRECTORY=/app/.duckdb_extensions

# Modo padrão: sse (REST + SSE para integração com orchestrator)
ENV MCP_SERVER_MODE=sse \
    MCP_LISTEN_PORT=8080 \
    LOG_LEVEL=INFO \
    LOGS_AWS_REGION=us-east-1 \
    LOGS_DEFAULT_LIMIT=500 \
    LOGS_MAX_LIMIT=1000 \
    LOGS_MAX_WINDOW_HOURS=24 \
    LOGS_MAX_PARTITIONS=24

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=5)"

CMD ["python", "-m", "logs_parquet"]
