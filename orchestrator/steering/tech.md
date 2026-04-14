# Stack & Guardrails

## Fontes
- Métricas: Prometheus → operator → VictoriaMetrics (PromQL)
- Logs: Fluent Bit → Splunk; e também S3 Parquet (Athena/Glue) para forense
- Traces: OpenTelemetry → Tempo
- Alertas/Dashboards: Grafana (Unified Alerting)
- Incidentes: ServiceNow → PostgreSQL (tabela `incidents_snow`)

## Integração via MCP
Cada sistema é acessado via MCP server (read-only). Tokens/segredos NUNCA devem ser commitados.
Use env vars referenciadas no mcp.json.

## Guardrails (obrigatório)
- Read-only por padrão; sem mutações.
- Sem PII em outputs; redigir quando necessário.
- Toda afirmação deve ter evidência: query/resultado/traceId/link.
- Se não houver correlação por labels (service/env/cluster/ns), apontar a lacuna e sugerir padronização.

## Contratos
- CaseFile JSON canônico (armazenável)
- Entity keys padrão: service.name, env, cluster, namespace, pod, deployment, trace_id

## Incidentes (PostgreSQL)
Tabela: `public.incidents_snow`
Campos: sys_id, number, short_description, opened_at, sys_created_by, impact, description,
category, subcategory, urgency, location, cmdb_ci, assignment_group, state, priority,
assignment_group_name, cmdb_ci_name, location_name, parent_incident.
- `cmdb_ci_name` é o CI do ServiceNow — **NEM SEMPRE corresponde ao `application_service`** real. Usar `_grafana_labels.application_service` do campo `description` como prioridade
- `parent_incident` permite rastrear incidentes filhos/relacionados

## Metadados dos Alertas Grafana
Labels enviados nos alertas:
- `alertname`: Nome da regra de alerta
- `application_service`: Componente (serviço, API, rotina) — chave canônica de correlação
- `business_capability`: Capacidade (Tecnológica ou Negócio)
- `business_domain`: Domínio (Tecnológico ou Negócio)
- `business_service`: Serviço ou Produto
- `owner_squad`: Equipe responsável (padrão ServiceNow/Jira)
- `owner_sre`: Equipe SRE responsável
- `grafana_folder`: Pasta/categoria do dashboard
- `Severidade`: P1, P2, P3
- `Datasource`: Origem dos dados (VictoriaMetrics, Tempo, Zabbix)
- `GIC`: True = Service Desk faz primeiro atendimento (apenas P2/Major)
- `Ops24by7`: True = gerenciado pelo time de Operações
- `SPoG`: Classificação interna do time responsável
- `Teams`: Canal no Microsoft Teams
