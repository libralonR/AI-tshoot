---
name: traces-catalog
description: Catálogo de queries TraceQL para coleta automática de traces no /investigate. Queries usam {service} como placeholder para application_service (mapeado para resource.service.name no Tempo).
---

# Traces Catalog

Catálogo de queries TraceQL executadas automaticamente pelo TracesAgent durante investigações.
O placeholder `{service}` é substituído pelo `application_service` do scope (corresponde a
`resource.service.name` em OTel).

## Convenções

- **Mapeamento canônico**: `application_service` (Grafana/Incidents/VM) ↔ `resource.service.name` (Tempo).
- **Tipos de query**:
  - `kind: search` — `traceql-search` (lista traces; cada entry pode definir `limit`)
  - `kind: metrics_instant` — `traceql-metrics-instant` (valor agregado em um instante)
  - `kind: metrics_range` — `traceql-metrics-range` (série temporal; cada entry define `step`)
- O TracesAgent passa `start`/`end` automaticamente conforme o `timeWindow` da investigação.

## Erros

```yaml
- name: error_traces
  category: errors
  kind: search
  query_template: '{ resource.service.name = "{service}" && status = error }'
  limit: 10
  description: Traces com status=error (top 10) — ponto de partida para investigar falhas

- name: error_count_total
  category: errors
  kind: metrics_instant
  query_template: '{ resource.service.name = "{service}" && status = error } | count()'
  description: Contagem total de spans com erro na janela
```

## Latência

```yaml
- name: slow_traces
  category: latency
  kind: search
  query_template: '{ resource.service.name = "{service}" && duration > 1s }'
  limit: 10
  description: Traces com duração superior a 1 segundo (top 10)

- name: latency_p99_by_span
  category: latency
  kind: metrics_range
  query_template: '{ resource.service.name = "{service}" } | quantile_over_time(duration, 0.99) by (span.name)'
  step: 5m
  description: P99 de latência por span name ao longo da janela

- name: latency_p95_by_span
  category: latency
  kind: metrics_range
  query_template: '{ resource.service.name = "{service}" } | quantile_over_time(duration, 0.95) by (span.name)'
  step: 5m
  description: P95 de latência por span name ao longo da janela
```

## Tráfego (throughput de spans)

```yaml
- name: span_rate
  category: traffic
  kind: metrics_range
  query_template: '{ resource.service.name = "{service}" } | rate()'
  step: 1m
  description: Taxa de spans por segundo do serviço

- name: span_rate_by_kind
  category: traffic
  kind: metrics_range
  query_template: '{ resource.service.name = "{service}" } | rate() by (span.kind)'
  step: 1m
  description: Taxa de spans agrupada por kind (server, client, internal, ...)
```

## HTTP

```yaml
- name: http_5xx_traces
  category: http
  kind: search
  query_template: '{ resource.service.name = "{service}" && span.http.status_code >= 500 }'
  limit: 10
  description: Traces com HTTP 5xx (top 10)

- name: http_4xx_rate
  category: http
  kind: metrics_range
  query_template: '{ resource.service.name = "{service}" && span.http.status_code >= 400 && span.http.status_code < 500 } | rate()'
  step: 5m
  description: Taxa de spans com HTTP 4xx
```

## Banco de dados

```yaml
- name: db_slow_queries
  category: database
  kind: search
  query_template: '{ resource.service.name = "{service}" && span.db.system != "" && duration > 500ms }'
  limit: 10
  description: Spans de banco com duração > 500ms (top 10)
```

## Notas

- O TracesAgent só executa o catálogo quando `application_service` está definido no scope.
- Cada query tem timeout próprio (15s), e falhas individuais não interrompem as demais.
- Resultados são truncados para no máximo 10 traces por search para evitar payloads grandes.
- Adicione queries customizadas (mensageria, RPC, cache) seguindo o mesmo padrão.
