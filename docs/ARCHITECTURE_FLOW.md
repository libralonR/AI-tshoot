# Observability Troubleshooting Copilot - Fluxo Completo

## Visão Geral

Este documento explica como funciona o fluxo completo desde o prompt do usuário até a resposta final com evidências, incluindo prompts, guardrails, specs e MCP servers.

## 1. Entrada do Usuário (Prompt)

O usuário pode iniciar uma investigação de 3 formas:

```
┌─────────────────────────────────────────────────────────────┐
│                    ENTRADA DO USUÁRIO                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Opção 1: Incident ID (ServiceNow)                          │
│  "Investigue o incidente INC0012345"                        │
│                                                              │
│  Opção 2: Alert UID (Grafana)                               │
│  "Analise o alerta abc123def456"                            │
│                                                              │
│  Opção 3: Sintoma Livre                                     │
│  "API Gateway está retornando 500 errors em produção"       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## 2. Orquestrador (Orchestrator Agent)

O orquestrador é o "cérebro" que coordena toda a investigação:

```
┌──────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR AGENT                         │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  1. Valida entrada (validateInput)                           │
│     ├─ Incident ID? → Formato INC + números                  │
│     ├─ Alert UID? → String não-vazia                         │
│     └─ Sintoma? → Extrai entidades (service, env, etc.)      │
│                                                               │
│  2. Cria CaseFile (dossiê da investigação)                   │
│     ├─ ID único (UUID)                                       │
│     ├─ Timestamp de criação                                  │
│     ├─ Scope (service, env, cluster, namespace)              │
│     └─ Time Window (janela de tempo)                         │
│                                                               │
│  3. Coordena Specialist Agents (em paralelo)                 │
│     ├─ Grafana Agent → Alertas + Dashboards                  │
│     ├─ Incidents Agent → Incidentes (PostgreSQL/AWS RDS)      │
│     ├─ Metrics Agent → VictoriaMetrics (futuro)               │
│     ├─ Logs Agent → Splunk + Athena (futuro)                  │
│     └─ Traces Agent → Tempo (futuro)                          │
│                                                               │
│  4. Correlaciona sinais (correlateSignals)                   │
│     └─ Usa labels padrão: service.name, env, cluster, etc.   │
│                                                               │
│  5. Gera hipóteses (generateHypotheses)                      │
│     └─ Ranqueia por confidence score (0.0 a 1.0)             │
│                                                               │
│  6. Aplica guardrails                                        │
│     ├─ Redação de PII                                        │
│     ├─ Validação read-only                                   │
│     └─ Evidência obrigatória                                 │
│                                                               │
│  7. Gera resposta estruturada                                │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

## 3. Specialist Agents + MCP Servers

Cada specialist agent consulta uma fonte de dados via MCP server:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SPECIALIST AGENTS                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  METRICS AGENT                                                │  │
│  │  ├─ Consulta: VictoriaMetrics MCP Server                     │  │
│  │  ├─ Query: PromQL                                             │  │
│  │  │   rate(http_requests_total{service="api-gateway"}[5m])    │  │
│  │  ├─ Detecta: Anomalias, spikes, threshold breaches           │  │
│  │  └─ Retorna: MetricEvidence com query + resultado            │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  LOGS AGENT                                                   │  │
│  │  ├─ Consulta: Splunk MCP Server + Athena MCP Server          │  │
│  │  ├─ Query: SPL (Splunk) ou SQL (Athena)                      │  │
│  │  │   index=app_logs service="api-gateway" level=ERROR        │  │
│  │  ├─ Extrai: Error patterns, stack traces                     │  │
│  │  └─ Retorna: LogEvidence com query + resultado (PII redacted)│  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  TRACES AGENT                                                 │  │
│  │  ├─ Consulta: Tempo MCP Server                               │  │
│  │  ├─ Query: TraceQL                                            │  │
│  │  │   {service.name="api-gateway" && status=error}            │  │
│  │  ├─ Identifica: Slow spans, error traces                     │  │
│  │  └─ Retorna: TraceEvidence com trace_id + span_id            │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  GRAFANA AGENT                                                │  │
│  │  ├─ Consulta: Grafana MCP Server (que você criou!)           │  │
│  │  ├─ Tools:                                                    │  │
│  │  │   • get_alert_details(alertUID)                           │  │
│  │  │   • find_firing_alerts(labels, dashboardUID)              │  │
│  │  │   • find_dashboards(labels, tags)                         │  │
│  │  │   • get_panel_link(dashboardUID, panelId, timeRange)      │  │
│  │  └─ Retorna: AlertEvidence + DashboardEvidence com links     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  INCIDENTS AGENT (PostgreSQL)                                 │  │
│  │  ├─ Consulta: Incidents PG MCP Server (AWS RDS)              │  │
│  │  ├─ Tools:                                                    │  │
│  │  │   • get_incident(number)                                  │  │
│  │  │   • search_incidents(application_service, priority, ...)  │  │
│  │  │   • get_related_incidents(number, application_service)    │  │
│  │  │   • get_incident_stats(application_service, days, ...)    │  │
│  │  ├─ Parseia labels do Grafana embutidas no description       │  │
│  │  └─ Retorna: IncidentEvidence com _grafana_labels + links    │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## 4. Correlação de Sinais

O orquestrador correlaciona evidências usando labels padrão:

```
┌─────────────────────────────────────────────────────────────┐
│                    CORRELAÇÃO DE SINAIS                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Labels Padrão:                                             │
│  ┌────────────────────────────────────────────────────┐     │
│  │  • application_service  (chave canônica)           │     │
│  │  • owner_squad                                     │     │
│  │  • severity (Severidade / priority)                │     │
│  │  • env / environment                               │     │
│  │  • cluster                                         │     │
│  │  • namespace                                       │     │
│  │  • pod                                             │     │
│  │  • deployment                                      │     │
│  │  • trace_id                                        │     │
│  └────────────────────────────────────────────────────┘     │
│                                                              │
│  Alias Mapping (normalização entre fontes):                 │
│  • application_service (Grafana) = cmdb_ci_name (PG)        │
│  • owner_squad (Grafana) = assignment_group_name (PG)       │
│  • Severidade (Grafana) = priority (PG)                     │
│                                                              │
│  Algoritmo:                                                  │
│  1. Extrai labels de cada evidência                         │
│  2. Cria correlation key: "service=api-gateway|env=prod"    │
│  3. Agrupa evidências com mesmo correlation key             │
│  4. Ajusta confidence score:                                │
│     • Múltiplos sinais correlacionados → +20% confidence    │
│     • Sinal isolado → -20% confidence                       │
│                                                              │
│  Gaps de Correlação:                                        │
│  • Se labels faltando → Cria CorrelationGap                 │
│  • Documenta qual label está faltando                       │
│  • Sugere padronização (ex: adicionar service.name)         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## 5. Guardrails (Segurança e Compliance)

Guardrails são aplicados em TODAS as etapas:

```
┌─────────────────────────────────────────────────────────────┐
│                         GUARDRAILS                           │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. READ-ONLY ENFORCEMENT                                    │
│     ├─ Todas as operações são somente leitura               │
│     ├─ Nenhum MCP server pode fazer mutações                │
│     ├─ NextSteps sempre têm readOnly=true                   │
│     └─ Validação: isMutationOperation() sempre retorna false│
│                                                              │
│  2. PII REDACTION (Redação de Dados Sensíveis)              │
│     ├─ Patterns detectados:                                 │
│     │  • Email: [EMAIL_REDACTED]                            │
│     │  • Phone: [PHONE_REDACTED]                            │
│     │  • IP Address: [IP_REDACTED]                          │
│     │  • API Key: [API_KEY_REDACTED]                        │
│     ├─ Aplicado em: logs, traces, incident descriptions     │
│     └─ Flag: evidence.redacted = true                       │
│                                                              │
│  3. EVIDENCE-BASED ASSERTIONS                                │
│     ├─ Toda afirmação DEVE ter evidência                    │
│     ├─ Evidência DEVE ter:                                  │
│     │  • Query executada OU                                 │
│     │  • Link para dashboard/alerta OU                      │
│     │  • Trace ID                                           │
│     └─ Sem "achismo" - tudo rastreável                      │
│                                                              │
│  4. CORRELATION GAP REPORTING                                │
│     ├─ Se labels faltando → Reporta gap                     │
│     ├─ Identifica qual label está ausente                   │
│     ├─ Sugere padronização                                  │
│     └─ Exemplo: "service.name missing in Splunk logs"       │
│                                                              │
│  5. TIMEOUT ENFORCEMENT                                      │
│     ├─ Cada query tem timeout de 15 segundos                │
│     ├─ Se timeout → Marca evidência como "timeout"          │
│     └─ Continua investigação com outras fontes              │
│                                                              │
│  6. AUDIT TRAIL                                              │
│     ├─ Toda investigação é logada                           │
│     ├─ Registra: input, queries, resultados, tempo          │
│     └─ Permite rastreabilidade completa                     │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## 6. Geração de Hipóteses

Com as evidências correlacionadas, o sistema gera hipóteses:

```
┌─────────────────────────────────────────────────────────────┐
│                    GERAÇÃO DE HIPÓTESES                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Algoritmo:                                                  │
│  1. Agrupa evidências por tipo                              │
│     ├─ Métricas: CPU spike, memory leak, error rate         │
│     ├─ Logs: Error patterns, stack traces                   │
│     ├─ Traces: Slow spans, error traces                     │
│     └─ Alertas: Firing alerts                               │
│                                                              │
│  2. Identifica padrões comuns                                │
│     ├─ Múltiplos sinais apontando para mesmo componente     │
│     ├─ Temporal correlation (eventos próximos no tempo)     │
│     └─ Causal relationships (trace → log → metric)          │
│                                                              │
│  3. Calcula confidence score                                 │
│     ├─ Base: 0.5                                            │
│     ├─ +0.2 por evidência correlacionada adicional          │
│     ├─ +0.1 se tem trace_id                                 │
│     ├─ +0.1 se tem alerta firing                            │
│     └─ Max: 1.0                                             │
│                                                              │
│  4. Gera hipótese estruturada                                │
│     ├─ suspectedComponent: "api-gateway"                    │
│     ├─ rootCause: "Memory leak causing OOM"                 │
│     ├─ evidenceIds: [uuid1, uuid2, uuid3]                   │
│     ├─ confidence: 0.85                                     │
│     └─ nextSteps: [query, dashboard link, runbook]          │
│                                                              │
│  5. Ranqueia por confidence                                  │
│     └─ Hipótese com maior confidence vem primeiro            │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## 7. CaseFile (Dossiê da Investigação)

O CaseFile é o "dossiê" completo da investigação:

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "createdAt": "2026-03-05T23:30:00Z",
  "updatedAt": "2026-03-05T23:30:15Z",
  
  "input": {
    "type": "ALERT_UID",
    "value": "abc123def456",
    "timestamp": "2026-03-05T23:30:00Z",
    "user": "sre-oncall@example.com"
  },
  
  "scope": {
    "serviceName": "api-gateway",
    "environment": "production",
    "cluster": "us-east-1",
    "namespace": "default",
    "pod": "api-gateway-7d8f9c5b6d-abc12"
  },
  
  "timeWindow": {
    "start": "2026-03-05T22:30:00Z",
    "end": "2026-03-05T23:30:00Z",
    "duration": "1h"
  },
  
  "evidence": [
    {
      "id": "evidence-001",
      "type": "METRIC_ANOMALY",
      "source": "victoriametrics-mcp",
      "query": "rate(http_requests_total{service=\"api-gateway\"}[5m])",
      "result": {"value": 1250, "threshold": 1000},
      "timestamp": "2026-03-05T23:25:00Z",
      "links": ["http://grafana/d/api-gateway/overview"],
      "confidence": 0.9,
      "redacted": false
    },
    {
      "id": "evidence-002",
      "type": "LOG_ERROR",
      "source": "splunk-mcp",
      "query": "index=app_logs service=\"api-gateway\" level=ERROR",
      "result": {"count": 450, "pattern": "OutOfMemoryError"},
      "timestamp": "2026-03-05T23:26:00Z",
      "links": ["http://splunk/search?q=..."],
      "confidence": 0.85,
      "redacted": true
    },
    {
      "id": "evidence-003",
      "type": "TRACE_ERROR",
      "source": "tempo-mcp",
      "query": "{service.name=\"api-gateway\" && status=error}",
      "result": {"trace_id": "abc123", "error_count": 120},
      "timestamp": "2026-03-05T23:27:00Z",
      "links": ["http://tempo/trace/abc123"],
      "confidence": 0.8,
      "redacted": false
    }
  ],
  
  "hypotheses": [
    {
      "id": "hypothesis-001",
      "description": "Memory leak in API Gateway causing OOM errors",
      "suspectedComponent": "api-gateway",
      "rootCause": "Memory leak in connection pool management",
      "evidenceIds": ["evidence-001", "evidence-002", "evidence-003"],
      "confidence": 0.88,
      "nextSteps": [
        {
          "action": "Check memory metrics",
          "description": "Verify memory usage trend over last 24h",
          "query": "container_memory_usage_bytes{pod=~\"api-gateway.*\"}",
          "link": "http://grafana/d/memory-dashboard",
          "readOnly": true,
          "priority": "HIGH"
        },
        {
          "action": "Review recent deployments",
          "description": "Check if recent code changes introduced leak",
          "link": "http://servicenow/changes?service=api-gateway",
          "readOnly": true,
          "priority": "MEDIUM"
        }
      ]
    }
  ],
  
  "correlationGaps": [
    {
      "missingLabel": "trace_id",
      "affectedSources": ["splunk-mcp"],
      "impact": "Cannot correlate logs with traces",
      "recommendation": "Add trace_id to log context using OpenTelemetry"
    }
  ],
  
  "auditTrail": [
    {
      "timestamp": "2026-03-05T23:30:00Z",
      "action": "Investigation started",
      "details": {"input": "abc123def456"}
    },
    {
      "timestamp": "2026-03-05T23:30:15Z",
      "action": "Investigation completed",
      "details": {"hypotheses_count": 1, "evidence_count": 3}
    }
  ]
}
```

## 8. Resposta Final ao Usuário

A resposta é estruturada e acionável:

```
┌─────────────────────────────────────────────────────────────┐
│                    RESPOSTA AO USUÁRIO                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  🔍 INVESTIGAÇÃO COMPLETA                                    │
│                                                              │
│  📋 Escopo:                                                  │
│     • Serviço: api-gateway                                  │
│     • Ambiente: production                                  │
│     • Cluster: us-east-1                                    │
│     • Janela: 2026-03-05 22:30 - 23:30 (1h)                 │
│                                                              │
│  🎯 HIPÓTESE PRINCIPAL (Confidence: 88%)                     │
│     Componente Suspeito: api-gateway                        │
│     Causa Raiz: Memory leak in connection pool management   │
│                                                              │
│  📊 EVIDÊNCIAS (3 encontradas):                              │
│                                                              │
│     1. Métrica: Request rate spike                          │
│        Query: rate(http_requests_total{...}[5m])            │
│        Resultado: 1250 req/s (threshold: 1000)              │
│        Link: http://grafana/d/api-gateway/overview          │
│                                                              │
│     2. Logs: OutOfMemoryError pattern                       │
│        Query: index=app_logs service="api-gateway" ERROR    │
│        Resultado: 450 errors encontrados                    │
│        Link: http://splunk/search?q=...                     │
│        ⚠️  PII redacted                                      │
│                                                              │
│     3. Traces: Error traces                                 │
│        Query: {service.name="api-gateway" && status=error}  │
│        Resultado: 120 error traces                          │
│        Trace ID: abc123                                     │
│        Link: http://tempo/trace/abc123                      │
│                                                              │
│  🚨 ALERTAS FIRING (2):                                      │
│     • High Memory Usage (api-gateway)                       │
│     • Error Rate Threshold Exceeded                         │
│                                                              │
│  📈 DASHBOARDS RELEVANTES:                                   │
│     • API Gateway Overview                                  │
│       http://grafana/d/api-gateway/overview                 │
│     • Memory Analysis Dashboard                             │
│       http://grafana/d/memory-dashboard                     │
│                                                              │
│  ⚡ PRÓXIMOS PASSOS (Read-Only):                             │
│                                                              │
│     1. [HIGH] Check memory metrics                          │
│        Verificar tendência de uso de memória nas últimas 24h│
│        Query: container_memory_usage_bytes{pod=~"api-*"}    │
│        Link: http://grafana/d/memory-dashboard              │
│                                                              │
│     2. [MEDIUM] Review recent deployments                   │
│        Verificar se mudanças recentes introduziram leak     │
│        Link: http://servicenow/changes?service=api-gateway  │
│                                                              │
│  ⚠️  GAPS DE CORRELAÇÃO:                                     │
│     • trace_id faltando nos logs do Splunk                  │
│       Recomendação: Adicionar trace_id ao log context       │
│       usando OpenTelemetry                                  │
│                                                              │
│  📝 CaseFile ID: 550e8400-e29b-41d4-a716-446655440000        │
│     Tempo de execução: 15.2s                                │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## 9. Fluxo Completo (Diagrama de Sequência)

```
Usuário          Orchestrator    Grafana Agent    Grafana MCP    Grafana API
   │                  │                │               │              │
   │ "Analise alerta  │                │               │              │
   │  abc123def456"   │                │               │              │
   ├─────────────────>│                │               │              │
   │                  │                │               │              │
   │                  │ 1. Valida input│               │              │
   │                  │ 2. Cria CaseFile              │              │
   │                  │                │               │              │
   │                  │ 3. Fetch alert │               │              │
   │                  ├───────────────>│               │              │
   │                  │                │ get_alert_    │              │
   │                  │                │  details()    │              │
   │                  │                ├──────────────>│              │
   │                  │                │               │ GET /api/v1/ │
   │                  │                │               │ provisioning/│
   │                  │                │               │ alert-rules/ │
   │                  │                │               │ abc123def456 │
   │                  │                │               ├─────────────>│
   │                  │                │               │              │
   │                  │                │               │ Alert Details│
   │                  │                │               │<─────────────┤
   │                  │                │ AlertEvidence │              │
   │                  │                │<──────────────┤              │
   │                  │ AlertEvidence  │               │              │
   │                  │<───────────────┤               │              │
   │                  │                │               │              │
   │                  │ 4. Parallel queries to other agents           │
   │                  │    (Metrics, Logs, Traces, etc.)              │
   │                  │                │               │              │
   │                  │ 5. Correlate signals                          │
   │                  │ 6. Generate hypotheses                        │
   │                  │ 7. Apply guardrails                           │
   │                  │                │               │              │
   │ Response with    │                │               │              │
   │ evidence + links │                │               │              │
   │<─────────────────┤                │               │              │
   │                  │                │               │              │
```

## 10. Steering Files e Contexto Persistente

Os steering files fornecem contexto persistente para o copilot:

```
.kiro/steering/
├── product.md          # Propósito, público-alvo, capacidades
├── tech.md             # Stack, guardrails, contratos
├── structure.md        # Estrutura do repositório
└── correlation-keys.md # Regras de correlação por labels
```

Esses arquivos são **automaticamente incluídos** em todas as interações com o copilot, garantindo que ele sempre:
- Siga os guardrails (read-only, PII redaction, evidence-based)
- Use os labels corretos para correlação
- Conheça a stack de observabilidade
- Entenda o propósito do sistema

## Resumo do Fluxo

1. **Usuário** envia prompt (Incident ID / Alert UID / Sintoma)
2. **Orchestrator** valida input e cria CaseFile
3. **Specialist Agents** consultam MCP servers em paralelo
4. **MCP Servers** executam queries nas fontes de dados (Grafana, VictoriaMetrics, Splunk, Tempo, ServiceNow, Athena)
5. **Orchestrator** correlaciona sinais usando labels padrão
6. **Guardrails** são aplicados (PII redaction, read-only, evidence-based)
7. **Hypotheses** são geradas e ranqueadas por confidence
8. **CaseFile** é persistido com audit trail completo
9. **Resposta** estruturada é retornada ao usuário com evidências, links e próximos passos

**Tudo é rastreável, seguro e baseado em evidências!**
