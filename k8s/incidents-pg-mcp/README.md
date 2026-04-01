# Incidents PostgreSQL MCP Server — Kubernetes Deployment

MCP server para buscar incidentes do ServiceNow armazenados em PostgreSQL (AWS RDS).
Read-only por design (guardrail).

## Tools disponíveis

| Tool | Descrição |
|------|-----------|
| `get_incident` | Busca incidente por número (INC0012345) |
| `search_incidents` | Busca por filtros (application_service, priority, state, category, date range) |
| `get_related_incidents` | Incidentes relacionados (mesmo CI ou parent_incident) |
| `get_incident_stats` | Estatísticas agrupadas por priority, category, state, assignment_group |

## Parsing do description

O campo `description` dos incidentes contém o corpo do alerta Grafana. O MCP parseia automaticamente e retorna:
- `_parsed.origin_url` / `_parsed.panel_url` / `_parsed.silence_url`
- `_parsed.alert_rule_uid`
- `_grafana_labels` (dict com todas as labels do alerta)

## Build

```bash
# Da raiz do projeto
docker build -t incidents-pg-mcp-server:latest \
  -f k8s/incidents-pg-mcp/Dockerfile .
```

## Configuração

`configmap.yaml`:
```yaml
data:
  pg-host: "your-rds-instance.region.rds.amazonaws.com"
  pg-port: "5432"
  pg-database: "incidents"
  pg-sslmode: "require"
```

`secret.yaml`:
```yaml
stringData:
  pg-user: "seu-user"
  pg-password: "sua-senha"
```

## Deploy

```bash
make deploy
make status
make logs
```

## Teste local (Docker Compose)

```bash
docker compose up -d incidents-pg-mcp
curl http://localhost:8082/health
curl -s -X POST http://localhost:8082/tools/search_incidents \
  -H "Content-Type: application/json" \
  -d '{"arguments": {"application_service": "payment-api"}}' | python3 -m json.tool
```

## Variáveis de ambiente

| Variável | Descrição | Default |
|----------|-----------|---------|
| `PG_HOST` | Host do PostgreSQL (AWS RDS) | `localhost` |
| `PG_PORT` | Porta | `5432` |
| `PG_DATABASE` | Nome do banco | `incidents` |
| `PG_USER` | Usuário | (obrigatório) |
| `PG_PASSWORD` | Senha | (obrigatório) |
| `PG_SSLMODE` | Modo SSL | `require` |
| `MCP_SERVER_MODE` | `stdio` ou `sse` | `stdio` |
| `MCP_LISTEN_PORT` | Porta HTTP (modo SSE) | `8080` |
