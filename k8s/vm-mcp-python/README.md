# VictoriaMetrics MCP Server (Python) — Kubernetes Deployment

MCP server Python que consulta a API HTTP do VictoriaMetrics diretamente.
Alternativa ao vm-mcp-proxy (que usa o Go binary como intermediário).

## Vantagens sobre o proxy

- Sem protocolo MCP SSE/HTTP no meio — fala HTTP REST direto com o VictoriaMetrics
- Sem sessões, sem handshake, sem race conditions
- Mesmo padrão do Grafana MCP e Incidents PG MCP

## Deploy

```bash
make build REGISTRY=your-registry TAG=v1.0.0
make push REGISTRY=your-registry TAG=v1.0.0
make deploy
```

## Configuração

| Variável | Descrição | Default |
|----------|-----------|---------|
| `VM_URL` | URL do VictoriaMetrics | `http://victoriametrics:8428` |
| `VM_BEARER_TOKEN` | Token de autenticação (opcional) | — |
| `VM_TIMEOUT` | Timeout para queries (s) | `30` |
| `MCP_LISTEN_PORT` | Porta do server | `8085` |

## Endpoints

| Endpoint | Descrição |
|----------|-----------|
| `GET /health` | Health check |
| `GET /tools` | Lista tools |
| `POST /tools/{name}` | Executa tool |

## Tools

| Tool | API VictoriaMetrics |
|------|-------------------|
| `query` | `/api/v1/query` |
| `query_range` | `/api/v1/query_range` |
| `metrics` | `/api/v1/label/__name__/values` |
| `labels` | `/api/v1/labels` |
| `label_values` | `/api/v1/label/{name}/values` |
| `series` | `/api/v1/series` |
| `tsdb_status` | `/api/v1/status/tsdb` |
| `alerts` | `/api/v1/alerts` |
| `rules` | `/api/v1/rules` |

## Teste

```bash
python mcp-servers/test_victoriametrics_mcp.py http://localhost:8085
python mcp-servers/test_victoriametrics_mcp.py http://localhost:8085 --verbose
```

## Trocar do proxy para este MCP

No configmap do orchestrator, mudar:
```yaml
vm-mcp-endpoint: "http://vm-mcp-python.observability.svc.cluster.local:8085"
```
