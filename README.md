# Observability Troubleshooting Copilot

> **📊 Code Review Completo Disponível**: Veja [docs/CODE_REVIEW_INDEX.md](docs/CODE_REVIEW_INDEX.md) para análise técnica completa, recomendações e roadmap executável.

Copilot de observabilidade que correlaciona métricas, alertas, incidentes e traces para acelerar triagem e reduzir MTTR.

## Arquitetura

```
┌─────────────────────────────────────────────────────────┐
│                     Orchestrator                         │
│  orchestrator.py │ config.py │ models.py │ guardrails.py│
│  correlation.py  │ hypothesis.py │ mcp_client.py        │
│  agents/grafana.py │ agents/incidents.py                │
└──────────┬──────────────┬──────────────┬────────────────┘
           │              │              │
     ┌─────▼─────┐ ┌─────▼──────┐ ┌────▼─────────────┐
     │ Grafana   │ │ Incidents  │ │ VictoriaMetrics   │
     │ MCP (SSE) │ │ PG MCP     │ │ MCP (Go binary)   │
     │ :8081     │ │ (SSE):8082 │ │ :8083             │
     └─────┬─────┘ └─────┬──────┘ └────┬─────────────┘
           │              │              │
     Grafana API    PostgreSQL       VictoriaMetrics
                    (AWS RDS)
```

## Estrutura do Repositório

```
├── orchestrator/          # Orchestrator (FastAPI, modular)
│   ├── orchestrator.py    # App principal + endpoints
│   ├── models.py          # Dataclasses e Pydantic models
│   ├── config.py          # Configuração e MCP endpoints
│   ├── mcp_client.py      # Cliente HTTP para MCP servers
│   ├── guardrails.py      # PII redaction, read-only enforcement
│   ├── correlation.py     # Correlação de sinais (application_service)
│   ├── hypothesis.py      # Geração e ranking de hipóteses
│   ├── agents/            # Specialist agents
│   │   ├── grafana.py     # Alertas e dashboards
│   │   └── incidents.py   # Incidentes PostgreSQL
│   ├── steering/          # Contexto persistente (baked na imagem)
│   └── prompts/           # System prompts (integração LLM)
├── mcp-servers/           # Implementações MCP
│   ├── grafana_v2.py      # Grafana MCP (SSE + REST)
│   └── incidents_pg.py    # Incidents PG MCP (psycopg3, SSE + REST)
├── k8s/                   # Manifestos Kubernetes
│   ├── orchestrator/      # Deploy do orchestrator
│   ├── grafana-mcp/       # Deploy do Grafana MCP
│   ├── incidents-pg-mcp/  # Deploy do Incidents PG MCP
│   └── vm-mcp/            # Deploy do VictoriaMetrics MCP (Go)
├── docs/                  # Documentação de arquitetura
└── docker-compose.yaml    # Stack completa para teste local
```

## Quick Start (Docker Compose)

```bash
# 1. Configurar variáveis
cp .env.example .env
# Editar .env com seus tokens/credenciais

# 2. Subir toda a stack
docker compose up -d

# 3. Testar
curl http://localhost:8080/health

# 4. Investigar um incidente
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "INCIDENT_ID",
    "value": "INC0012345",
    "user": "sre-oncall"
  }'
```

## MCP Servers

| Servidor | Imagem | Porta Local | Modo |
|----------|--------|-------------|------|
| Grafana MCP | `grafana_v2.py` | 8081 | SSE (`MCP_SERVER_MODE=sse`) |
| Incidents PG MCP | `incidents_pg.py` (psycopg3) | 8082 | SSE (`MCP_SERVER_MODE=sse`) |
| VictoriaMetrics MCP | Go binary oficial | 8083 | HTTP (`MCP_SERVER_MODE=http`) |
| Orchestrator | `orchestrator.py` (FastAPI) | 8080 | HTTP nativo |

Todos os MCP servers suportam modo dual:
- **stdio**: para uso local com Kiro/IDE
- **SSE**: para Docker/K8s, expõe `/tools/{tool_name}`, `/health`, `/tools`

## Correlação

Chave canônica: `application_service`
- Grafana: label `application_service`
- Incidentes PG: `application_service` extraído das labels do Grafana no campo `description` (prioridade), `cmdb_ci_name` como fallback
- Hierarquia: `business_capability → business_domain → business_service → application_service`

## Documentação

- [Orchestrator README](orchestrator/README.md)
- [Orchestrator Quick Start](orchestrator/QUICKSTART.md)
- [K8s Orchestrator Deploy](k8s/orchestrator/README.md)
- [Fluxo de Arquitetura](docs/ARCHITECTURE_FLOW.md)
- [Arquitetura K8s](docs/K8S_ARCHITECTURE.md)
