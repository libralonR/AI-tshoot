# Estrutura do repositório

```
├── orchestrator/              # Orchestrator (FastAPI, modular)
│   ├── orchestrator.py        # App principal + endpoints (/investigate, /chat, /metrics)
│   ├── models.py              # Dataclasses e Pydantic models
│   ├── config.py              # Configuração e MCP endpoints
│   ├── mcp_client.py          # Cliente HTTP para MCP servers
│   ├── guardrails.py          # PII redaction, read-only enforcement
│   ├── correlation.py         # Correlação de sinais (application_service)
│   ├── hypothesis.py          # Geração e ranking de hipóteses
│   ├── llm_client.py          # LLM client (OpenAI GPT-4o, function calling)
│   ├── metrics.py             # Métricas Prometheus (observa_*)
│   ├── agents/                # Specialist agents
│   │   ├── grafana.py         # Alertas e dashboards
│   │   ├── incidents.py       # Incidentes PostgreSQL
│   │   └── metrics.py         # VictoriaMetrics PromQL
│   ├── steering/              # Contexto persistente (baked na imagem)
│   │   └── metrics-catalog.md # Catálogo de queries PromQL (golden signals)
│   ├── prompts/               # System prompts (integração LLM)
│   └── specs/                 # Design specs
├── mcp-servers/               # Implementações MCP
│   ├── grafana_v2.py          # Grafana MCP (SSE + REST)
│   ├── incidents_pg.py        # Incidents PG MCP (psycopg3, SSE + REST)
│   ├── victoriametrics_mcp.py # VictoriaMetrics MCP (Python, REST direto)
│   └── vm_mcp_proxy.py        # VM MCP Proxy (traduz REST → MCP SSE/HTTP)
├── ui/                        # Streamlit UI (interface gráfica)
├── k8s/                       # Manifestos Kubernetes
│   ├── orchestrator/          # Deploy do orchestrator (namespace: copilot)
│   ├── grafana-mcp/           # Deploy do Grafana MCP (namespace: observability)
│   ├── incidents-pg-mcp/      # Deploy do Incidents PG MCP
│   ├── vm-mcp/                # Deploy do VictoriaMetrics MCP (Go binary)
│   ├── vm-mcp-proxy/          # Deploy do VM MCP Proxy
│   ├── vm-mcp-python/         # Deploy do VM MCP Python (alternativa ao proxy)
│   └── ui/                    # Deploy da Streamlit UI (namespace: copilot)
├── docs/                      # Documentação de arquitetura
│   └── ADDING_MCP_SERVERS.md  # Guia para adicionar novos MCPs
├── .kiro/steering/            # Steering files (workspace-level)
├── .kiro/settings/mcp.json    # Configuração MCP servers (local dev)
├── docker-compose.yaml        # Stack completa para teste local
└── .env.example               # Template de variáveis de ambiente
```

Padrão: tudo que o agente usar deve estar documentado em steering/runbooks ou em specs.
Para adicionar novos MCP servers, seguir `docs/ADDING_MCP_SERVERS.md`.
