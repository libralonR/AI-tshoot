FROM python:3.12-slim

WORKDIR /app

RUN adduser --disabled-password --uid 1000 mcp

COPY mcp-servers/vm_mcp_proxy.py .
COPY k8s/vm-mcp-proxy/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

USER mcp

EXPOSE 8084

ENTRYPOINT ["python", "vm_mcp_proxy.py"]
