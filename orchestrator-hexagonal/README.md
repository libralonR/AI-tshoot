# Orchestrator — Versão Hexagonal

Esta é a **reorganização hexagonal** do orchestrator, mantida em paralelo com
a versão de produção em `orchestrator/`. Ambas convivem; a versão atual
continua intacta para testes.

## Por que duas pastas?

- `orchestrator/` — versão estável, em produção, **não foi tocada**.
- `orchestrator-hexagonal/` — mesma funcionalidade, organizada em
  Ports & Adapters conforme `docs/HEXAGONAL_ROADMAP.md`.

Os contratos externos são idênticos:

| Contrato | Mesmo nas duas versões |
|----------|------------------------|
| Rotas HTTP | `/investigate`, `/chat`, `/health`, `/metrics`, `/steering`, `/casefile/{id}` |
| Schemas | `InvestigateRequest`, `InvestigateResponse`, `ChatRequest`, `ChatResponse` |
| Métricas Prometheus | `observa_*` (todas) |
| Env vars | `GRAFANA_MCP_ENDPOINT`, `VM_MCP_ENDPOINT`, `TEMPO_MCP_ENDPOINT`, `INCIDENTS_PG_MCP_ENDPOINT`, `OPENAI_*`, `PORT` |
| Wire protocol MCP | REST `/tools/{name}` + JSON-RPC `/mcp` (igual) |

Logo MCP servers, UI Streamlit, Grafana e K8s manifests continuam funcionando
sem alteração — basta apontar a imagem para a tag desta versão.

## Estrutura

```
orchestrator-hexagonal/
├── domain/                     # núcleo puro, sem dependências externas
│   ├── models.py               # CaseFile, Evidence, Hypothesis, ...
│   ├── correlation.py          # CorrelationEngine
│   ├── hypothesis.py           # HypothesisGenerator
│   └── guardrails.py           # PII redaction, read-only enforcement
│
├── application/                # casos de uso e contratos
│   ├── ports/                  # interfaces (Protocol)
│   │   ├── alert_source.py
│   │   ├── incident_source.py
│   │   ├── metric_source.py
│   │   ├── trace_source.py
│   │   ├── llm_provider.py
│   │   └── case_file_repository.py
│   └── use_cases/
│       ├── investigate.py      # InvestigateUseCase
│       └── chat.py             # ChatUseCase
│
├── infrastructure/             # adapters concretos e clientes
│   ├── config.py               # carregamento de env, steering, catálogo
│   ├── mcp_client.py           # cliente HTTP MCP (REST + JSON-RPC)
│   ├── prometheus_metrics.py   # métricas observa_*
│   └── adapters/
│       ├── grafana_alert_adapter.py
│       ├── pg_incident_adapter.py
│       ├── vm_metric_adapter.py
│       ├── tempo_trace_adapter.py
│       ├── openai_llm_adapter.py
│       └── inmemory_repo.py
│
└── api/                        # driving adapters (HTTP)
    ├── main.py                 # entrypoint FastAPI
    ├── dependencies.py         # DI simples
    └── http/
        └── routes.py           # endpoints
```

## Build & Run

```bash
# Local
cd orchestrator-hexagonal
pip install -r requirements.txt
PYTHONPATH=. uvicorn api.main:app --host 0.0.0.0 --port 8080

# Docker
docker build -t orchestrator-hexagonal:latest .
docker run -p 8080:8080 \
  -e OPENAI_API_KEY=... \
  -e GRAFANA_MCP_ENDPOINT=... \
  -e VM_MCP_ENDPOINT=... \
  -e INCIDENTS_PG_MCP_ENDPOINT=... \
  -e TEMPO_MCP_ENDPOINT=... \
  orchestrator-hexagonal:latest
```

## Estado da reforma

Esta versão entrega de uma vez **as 5 fases** do roadmap hexagonal aplicadas
exclusivamente ao orchestrator:

- [x] Fase 1 — Ports formalizadas com `typing.Protocol`
- [x] Fase 2 — Use Cases extraídos (`InvestigateUseCase`, `ChatUseCase`)
- [x] Fase 3 — Adapters em `infrastructure/adapters/`
- [x] Fase 4 — Inversão de dependência via `api/dependencies.py`
- [ ] Fase 5 — Suite de testes do domínio (TODO em `tests/`)

A camada de testes (`tests/`) é o próximo passo natural; ports já permitem
adapters fake/in-memory triviais.
