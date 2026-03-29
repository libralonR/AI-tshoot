# Estrutura do Orchestrator (Container /app)

```
/app/
├── orchestrator.py          # FastAPI app + endpoints + Orchestrator class
├── models.py                # Dataclasses e Enums (Input, Scope, Evidence, CaseFile...)
├── config.py                # Config, MCP endpoints, label aliases, steering loader
├── mcp_client.py            # MCPClient (comunicação HTTP com MCP servers)
├── guardrails.py            # PII redaction, read-only enforcement, evidence validation
├── correlation.py           # CorrelationEngine (normalização, correlação, gaps)
├── hypothesis.py            # HypothesisGenerator (gera e rankeia hipóteses)
├── agents/
│   ├── __init__.py
│   ├── grafana.py           # GrafanaAgent (alertas, dashboards)
│   └── incidents.py         # IncidentsAgent (incidentes PostgreSQL)
├── steering/                # Contexto persistente (baked na imagem)
│   ├── product.md
│   ├── tech.md
│   ├── correlation-keys.md
│   └── structure.md
├── prompts/                 # System prompts (para integração LLM)
│   ├── orchestrator-prompt.md
│   ├── grafana-agent-prompt.md
│   └── incidents-agent-prompt.md
├── specs/
│   └── design-summary.md
├── requirements.txt
└── Dockerfile
```

## Fluxo de imports

```
orchestrator.py
  ├── config.py (Config)
  ├── models.py (Input, CaseFile, etc.)
  ├── correlation.py (CorrelationEngine)
  ├── hypothesis.py (HypothesisGenerator)
  ├── guardrails.py (Guardrails)
  └── agents/ (GrafanaAgent, IncidentsAgent)
        └── mcp_client.py (MCPClient)
```

## Endpoints FastAPI

| Endpoint              | Método | Descrição                        |
|-----------------------|--------|----------------------------------|
| `/health`             | GET    | Health check                     |
| `/steering`           | GET    | Steering files carregados        |
| `/investigate`        | POST   | Inicia investigação (CaseFile)   |
| `/casefile/{id}`      | GET    | Busca CaseFile (TODO: storage)   |
