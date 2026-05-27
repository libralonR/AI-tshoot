# Changelog

Todas as mudanças notáveis neste projeto são documentadas aqui.

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).
Versionamento segue [Semantic Versioning](https://semver.org/lang/pt-BR/):
- **MAJOR** — breaking change na API ou arquitetura
- **MINOR** — nova funcionalidade retrocompatível
- **PATCH** — bugfix retrocompatível

---

## [Não lançado]

> Mudanças em desenvolvimento que ainda não foram lançadas.

### Adicionado
- ...

### Modificado
- ...

### Corrigido
- ...

---

## [1.1.0] — 2026-04-13

> **Tipo**: Minor — melhorias de observabilidade, resiliência e busca de incidentes  
> **Score Code Review**: 8.0/10 (↑ de 7.5)

### Adicionado

- **`orchestrator/metrics.py`** — Métricas Prometheus com prefixo `observa_*`
  - `observa_investigation_duration_seconds` / `observa_investigation_total`
  - `observa_mcp_call_duration_seconds` / `observa_mcp_call_total`
  - `observa_chat_duration_seconds` / `observa_chat_sessions_active`
  - `observa_llm_call_duration_seconds` / `observa_llm_tokens_total`
  - `observa_pii_redactions_total`
- **`orchestrator/diagnose_llm.py`** — Script de diagnóstico de conectividade LLM (DNS, TCP, HTTPS, API)
- **`k8s/orchestrator/configmap-llm-timeout.yaml`** — ConfigMap com timeouts configuráveis via env vars
- **`mcp-servers/test_incidents_pg.py`** — Suite de 18 testes automatizados (12 REST + 6 Direct) com exit codes para CI/CD
- **`docs/LLM_GATEWAY_TROUBLESHOOTING.md`** — Guia completo para diagnóstico de conectividade LLM
- **`docs/INCIDENTS_SEARCH_STRATEGY.md`** — Documentação da estratégia de busca priorizada no campo `description`
- **`docs/LOGGING_IMPROVEMENTS_SUMMARY.md`** — Resumo executivo das melhorias de logging

### Modificado

- **`orchestrator/llm_client.py`**
  - Timeouts configuráveis via `OPENAI_TIMEOUT` (padrão: 60s) e `OPENAI_CONNECT_TIMEOUT` (padrão: 10s)
  - Tratamento diferenciado `ConnectTimeout` vs `APITimeout` com mensagens acionáveis
  - SSL `verify=False` para ambientes corporativos com proxy
  - Retry reduzido de 3 para 2 tentativas
- **`orchestrator/orchestrator.py`**
  - Logging estruturado com formato `[function] message | key=value` em todas as funções críticas
  - Timing de execução em `_gather_signals`, `_execute_tool`, `chat_endpoint`
  - Correlação de chaves de incidentes nos logs (`ci_name`, `inc_number`)
- **`orchestrator/mcp_client.py`**
  - Logs separando tempo total de tempo MCP (network overhead visível)
  - Tamanho de resposta em bytes
  - Erro HTTP com status code e response body
- **`mcp-servers/incidents_pg.py`**
  - Busca **prioriza** campo `description` sobre `cmdb_ci_name` (resolve gap quando `cmdb_ci_name` está vazio)
  - Busca estruturada por labels Grafana no formato `- application_service=<valor>` no bloco `Labels:`
  - Suporte a filtros diretos: `application_service`, `business_capability`, `business_domain`, `business_service`, `owner_squad`, `owner_sre`
  - Filtros combinados (AND) para busca mais precisa
  - Deduplicação automática entre buscas por `description` e `cmdb_ci_name`
  - Logging estruturado com prefixos `[function_name]` em todas as 4 funções principais
  - Parsing de labels do Grafana via `parse_description()`

### Corrigido

- **Correlação de incidentes com `cmdb_ci_name` vazio** — busca agora encontra incidentes via bloco `Labels:` do campo `description` que sempre é preenchido pelo Grafana
- **Timeout de conexão TCP ao LLM Gateway** — resolvido com timeouts configuráveis, mensagens de erro claras e documentação de troubleshooting

### Problemas conhecidos

- Sem testes automatizados unitários (pytest) — planejado para v1.2.0
- Sem rate limiting no `/chat` — planejado para v1.2.0
- Sem circuit breaker para MCP servers — planejado para v1.2.0
- CaseFile storage apenas in-memory (não persiste após restart) — planejado para v1.4.0
- Busca de incidentes não é executada quando `application_service` está ausente nos filtros — planejado para v1.3.0

---

## [1.0.0] — 2026-03-05

> **Tipo**: Major — release inicial do PoC  
> **Score Code Review**: 7.5/10

### Adicionado

- **Orchestrator** (Python / FastAPI) com endpoints:
  - `POST /investigate` — análise estruturada sem LLM
  - `POST /chat` — análise conversacional com LLM (function calling)
  - `GET /health` — health check
  - `GET /casefile/{id}` — recuperar CaseFile (in-memory)
- **GrafanaAgent** — busca alertas firing e dashboards via Grafana MCP
- **IncidentsAgent** — busca incidentes via Incidents PG MCP (PostgreSQL / ServiceNow)
- **CorrelationEngine** — normalização de labels, chave de correlação, detecção de gaps
- **HypothesisGenerator** — geração e ranking de hipóteses por confidence score (0.0–1.0)
- **Guardrails**
  - PII redaction: email, phone, IP address, API keys
  - Read-only enforcement em todos os `NextStep`
  - Evidence traceability validation
- **MCPClient** — cliente HTTP para comunicação REST com MCP servers
- **LLMClient** — integração OpenAI com function calling para `/chat`
- **CaseFile model** — dossiê completo com `scope`, `evidence`, `hypotheses`, `correlationGaps`, `auditTrail`
- **Grafana MCP Server** (`mcp-servers/grafana_v2.py`)
  - `get_alert_details(alertUID)`
  - `find_firing_alerts(labels, dashboardUID)`
  - `find_dashboards(labels, tags)`
  - `get_panel_link(dashboardUID, panelId, timeRange)`
- **Incidents PG MCP Server** (`mcp-servers/incidents_pg.py`)
  - `get_incident(number)`
  - `search_incidents(application_service, priority, state, limit)`
  - `get_related_incidents(number, application_service, time_window_hours)`
  - `get_incident_stats(application_service, days, group_by)`
- **Steering files** (`.kiro/steering/`) — contexto persistente: `product.md`, `tech.md`, `correlation-keys.md`, `structure.md`
- **System prompts** (`orchestrator/prompts/`) — prompts especializados por agente
- **K8s manifests** — deployments, services, configmaps, secrets, network policies, HPA, PDB
- **Docker Compose** — ambiente local completo com todos os serviços

### Capacidades v1.0.0

| Capacidade | Detalhe |
|------------|---------|
| Entradas suportadas | `INCIDENT_ID`, `ALERT_UID`, `SYMPTOM` |
| Fontes de dados | Grafana API, PostgreSQL (ServiceNow) |
| Correlação | Por `application_service` como chave canônica |
| Busca paralela | Grafana + Incidents PG simultâneos |
| Hipóteses | Rankeadas por confidence score |
| Guardrails | Read-only, PII redaction, evidence-based assertions |

---

<!--
## Template para próximas releases

## [X.Y.Z] — YYYY-MM-DD

> **Tipo**: Major / Minor / Patch  
> **Sprint**: Sprint N — Nome  

### Adicionado
- ...

### Modificado
- ...

### Corrigido
- ...

### Removido
- ...

### Problemas conhecidos
- ...
-->