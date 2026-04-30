FROM python:3.12-slim

WORKDIR /app

RUN adduser --disabled-password --uid 1000 mcp

COPY mcp-servers/victoriametrics_mcp.py .
COPY k8s/vm-mcp-python/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

USER mcp

EXPOSE 8085

ENTRYPOINT ["python", "victoriametrics_mcp.py"]
