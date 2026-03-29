# Orchestrator

Orquestrador modular que coordena specialist agents, aplica guardrails e gera investigações estruturadas (CaseFile).

## Estrutura Modular

```
orchestrator/
├── orchestrator.py      # FastAPI app + endpoints + classe Orchestrator
├── models.py            # Dataclasses (CaseFile, Evidence, Hypothesis...) + Pydantic (API)
├── config.py            # Config, MCP endpoints, label aliases, steering loader
├── mcp_client.py        # MCPClient (HTTP com MCP servers via /tools/{tool_name})
├── guardrails.py        # PII redaction, read-only enforcement, evidence validation
├── correlation.py       # CorrelationEngine (normalização, correlação, gaps)
├── hypothesis.py        # HypothesisGenerator (gera e rankeia hipóteses)
├── agents/
│   ├── grafana.py       # GrafanaAgent (alertas, dashboards)
│   └── incidents.py     # IncidentsAgent (incidentes PostgreSQL)
├── steering/            # Contexto persistente (baked na imagem Docker)
├── prompts/             # System prompts (integração LLM)
├── specs/               # Design specs
├── requirements.txt
└── Dockerfile
```

## Fluxo de Investigação

1. Recebe request em `/investigate` (INCIDENT_ID, ALERT_UID ou SYMPTOM)
2. Determina escopo e janela de tempo (via MCP servers)
3. Coleta sinais em paralelo (GrafanaAgent + IncidentsAgent)
4. Correlaciona evidências usando `application_service` como chave canônica
5. Gera hipóteses rankeadas por confidence
6. Aplica guardrails (PII redaction, read-only, traceability)
7. Retorna CaseFile completo

## API Endpoints

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/health` | GET | Health check |
| `/steering` | GET | Steering files carregados + standard labels |
| `/investigate` | POST | Investigação principal (retorna CaseFile) |
| `/casefile/{id}` | GET | Busca CaseFile por ID (TODO: storage) |

### POST /investigate

```json
{
  "input_type": "INCIDENT_ID | ALERT_UID | SYMPTOM",
  "value": "INC0012345",
  "user": "sre-oncall",
  "filters": {
    "application_service": "api-gateway",
    "owner_squad": "squad-pagamentos",
    "severidade": "P1",
    "business_capability": "Pagamentos",
    "grafana_folder": "SRE",
    "alertname": "HighErrorRate"
  }
}
```

O campo `filters` é opcional e aceita qualquer combinação das chaves acima para refinar a busca.

### Exemplos curl

```bash
# Health check
curl http://localhost:8080/health

# Investigar por incidente
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{"input_type": "INCIDENT_ID", "value": "INC0012345"}'

# Investigar por alerta Grafana
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{"input_type": "ALERT_UID", "value": "abc123def456"}'

# Investigar sintoma com filtros
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "SYMPTOM",
    "value": "Erro 500 no checkout",
    "filters": {
      "application_service": "checkout-api",
      "owner_squad": "squad-pagamentos",
      "severidade": "P1"
    }
  }'
```

## Configuração

### Variáveis de Ambiente (MCP Endpoints)

| Variável | Default (K8s) | Docker Compose |
|----------|---------------|----------------|
| `GRAFANA_MCP_ENDPOINT` | `http://grafana-mcp-server.observability.svc.cluster.local:8080` | `http://grafana-mcp:8080` |
| `INCIDENTS_PG_MCP_ENDPOINT` | `http://incidents-pg-mcp-server.observability.svc.cluster.local:8080` | `http://incidents-pg-mcp:8080` |
| `VM_MCP_ENDPOINT` | `http://victoriametrics-mcp-server.observability.svc.cluster.local:8080` | `http://vm-mcp:8080` |
| `PORT` | `8080` | `8080` |

### Correlação de Labels

| Original | Fonte | Canônico |
|----------|-------|----------|
| `application_service` | Grafana | `application_service` |
| `cmdb_ci_name` | Incidentes PG | `application_service` |
| `owner_squad` | Grafana | `owner_squad` |
| `assignment_group_name` | Incidentes PG | `owner_squad` |
| `Severidade` | Grafana | `severity` |
| `priority` | Incidentes PG | `severity` |

## Guardrails

- **Read-only**: todas as operações são somente leitura
- **PII redaction**: emails, IPs, API keys, telefones
- **Evidence-based**: toda afirmação tem query/link/trace_id
- **Correlation gaps**: identifica labels faltando e sugere padronização

## Desenvolvimento Local

```bash
# Via Docker Compose (recomendado)
docker compose up -d
curl http://localhost:8080/health

# Ou direto com Python
cd orchestrator
pip install -r requirements.txt
python orchestrator.py
```

## Docker Build

```bash
cd orchestrator
docker build -t orchestrator:latest .
```

O Dockerfile copia todos os módulos individualmente:
```dockerfile
COPY orchestrator.py config.py models.py mcp_client.py guardrails.py correlation.py hypothesis.py ./
COPY agents/ ./agents/
COPY steering/ ./steering/
COPY prompts/ ./prompts/
```

## Referências

- [Quick Start](QUICKSTART.md)
- [K8s Deploy](../k8s/orchestrator/README.md)
- [Fluxo de Arquitetura](../docs/ARCHITECTURE_FLOW.md)
