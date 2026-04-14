# VM MCP Proxy - Kubernetes Deployment

Proxy adapter que traduz REST `/tools/{name}` para protocolo MCP SSE/HTTP,
permitindo que o orchestrator se comunique com o `mcp-victoriametrics` (Go binary).

## Arquitetura

```
Orchestrator  --REST /tools/{name}-->  vm-mcp-proxy  --MCP SSE-->  vm-mcp-server (Go)
   :8080                                  :8084                        :8080
```

## Pré-requisitos

- VM MCP Server (Go binary) deployado: `k8s/vm-mcp/`
- Namespace `observability` criado

## Build

```bash
make build REGISTRY=your-registry TAG=v1.0.0
make push REGISTRY=your-registry TAG=v1.0.0
```

## Deploy

```bash
# Atualizar imagem no deployment.yaml
# Atualizar vm-mcp-upstream no configmap.yaml se necessário

make deploy
# ou
kubectl apply -k .
```

## Configuração

| Variável | Descrição | Default |
|----------|-----------|---------|
| `VM_MCP_UPSTREAM` | URL do VM MCP Server (Go) | `http://vm-mcp-server.observability.svc.cluster.local:8080` |
| `VM_MCP_MODE` | Modo de comunicação: `sse` ou `http` | `sse` |
| `PROXY_LISTEN_PORT` | Porta do proxy | `8084` |
| `PROXY_TIMEOUT` | Timeout para chamadas upstream (s) | `30` |

## Endpoints

| Endpoint | Descrição |
|----------|-----------|
| `GET /health` | Health check (verifica upstream) |
| `GET /tools` | Lista tools disponíveis no VM MCP |
| `POST /tools/{name}` | Executa uma tool (mesma interface dos outros MCPs) |

## Verificação

```bash
# Health check
curl http://vm-mcp-proxy:8084/health

# Listar tools
curl http://vm-mcp-proxy:8084/tools

# Executar query
curl -X POST http://vm-mcp-proxy:8084/tools/query \
  -H "Content-Type: application/json" \
  -d '{"arguments": {"query": "up"}}'
```
