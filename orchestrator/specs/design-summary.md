# Design Summary — Observability Troubleshooting Copilot

Resumo do design para referência em runtime. O design completo está em
`.kiro/specs/observability-troubleshooting-copilot/design.md`.

## Arquitetura

Padrão orchestrator-specialist: um orchestrator central coordena agents especialistas
que consultam fontes de observabilidade via MCP servers (read-only).

## Fluxo

1. Input: Incident ID (ServiceNow) / Alert UID (Grafana) / Sintoma livre
2. Scope: Extrair `application_service`, ambiente, cluster, time window
3. Coleta paralela: Grafana alerts + Incidentes PG (+ futuro: métricas, logs, traces)
4. Correlação: Normalizar labels via alias mapping, agrupar por `application_service`
5. Hipóteses: Rankear por confidence baseado em evidências cruzadas
6. Resposta: CaseFile com evidências, hipóteses, gaps e próximos passos

## Agents implementados

- GrafanaAgent: alertas firing, detalhes de alerta, dashboards, panel links
- IncidentsAgent: busca por número, filtros, relacionados, estatísticas

## Agents futuros

- MetricsAgent: VictoriaMetrics (PromQL)
- LogsAgent: Splunk (SPL)
- TracesAgent: Tempo (TraceQL)
- AthenaAgent: S3 Parquet (SQL)

## Guardrails

- Read-only: nenhuma mutação permitida
- PII redaction: emails, IPs, telefones, API keys
- Evidence-based: toda afirmação com query/resultado/link
- Correlation gaps: reportar labels faltando

## Correlação

Chave canônica: `application_service`
Alias mapping normaliza `cmdb_ci_name`, `service.name`, `service` → `application_service`

## CaseFile

Estrutura canônica JSON com: id, input, scope, timeWindow, evidence[], hypotheses[],
correlationGaps[], auditTrail[].
