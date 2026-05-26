---
name: metrics-catalog
description: Catálogo de queries PromQL/MetricsQL para coleta automática de métricas no /investigate. Queries usam {service} como placeholder para application_service.
---

# Metrics Catalog

Catálogo de queries executadas automaticamente pelo MetricsAgent durante investigações.
O placeholder `{service}` é substituído pelo `application_service` do scope.

## Golden Signals

### Latência
```yaml
- name: request_latency_p99
  category: golden_signal
  query_template: histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{application_service="{service}"}[5m])) by (le))
  description: Latência P99 das requisições HTTP

- name: request_latency_p95
  category: golden_signal
  query_template: histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{application_service="{service}"}[5m])) by (le))
  description: Latência P95 das requisições HTTP

- name: request_latency_avg
  category: golden_signal
  query_template: sum(rate(http_request_duration_seconds_sum{application_service="{service}"}[5m])) / sum(rate(http_request_duration_seconds_count{application_service="{service}"}[5m]))
  description: Latência média das requisições HTTP
```

### Tráfego (Throughput)
```yaml
- name: request_rate
  category: golden_signal
  query_template: sum(rate(http_requests_total{application_service="{service}"}[5m]))
  description: Taxa de requisições por segundo

- name: request_rate_by_status
  category: golden_signal
  query_template: sum(rate(http_requests_total{application_service="{service}"}[5m])) by (status_code)
  description: Taxa de requisições por status code
```

### Erros
```yaml
- name: error_rate
  category: golden_signal
  query_template: sum(rate(http_requests_total{application_service="{service}", status_code=~"5.."}[5m])) / sum(rate(http_requests_total{application_service="{service}"}[5m])) * 100
  description: Taxa de erros HTTP 5xx (percentual)

- name: error_count
  category: golden_signal
  query_template: sum(increase(http_requests_total{application_service="{service}", status_code=~"5.."}[5m]))
  description: Contagem de erros HTTP 5xx nos últimos 5 minutos
```

### Saturação
```yaml
- name: cpu_usage
  category: golden_signal
  query_template: sum(rate(container_cpu_usage_seconds_total{pod=~"{service}.*"}[5m])) by (pod)
  description: Uso de CPU por pod

- name: memory_usage
  category: golden_signal
  query_template: sum(container_memory_working_set_bytes{pod=~"{service}.*"}) by (pod)
  description: Uso de memória por pod

- name: memory_usage_percent
  category: golden_signal
  query_template: sum(container_memory_working_set_bytes{pod=~"{service}.*"}) / sum(container_spec_memory_limit_bytes{pod=~"{service}.*"}) * 100
  description: Percentual de uso de memória vs limite
```

## Infraestrutura

```yaml
- name: pod_restarts
  category: infrastructure
  query_template: sum(increase(kube_pod_container_status_restarts_total{pod=~"{service}.*"}[1h])) by (pod)
  description: Restarts de pods na última hora

- name: pod_status
  category: infrastructure
  query_template: kube_pod_status_phase{pod=~"{service}.*"}
  description: Status dos pods (Running, Pending, Failed, etc.)

- name: replicas_available
  category: infrastructure
  query_template: kube_deployment_status_replicas_available{deployment=~"{service}.*"}
  description: Réplicas disponíveis do deployment
```

## Notas

- Queries usam `application_service` como label padrão (correlação com Grafana/Incidents)
- Para pods, usa `pod=~"{service}.*"` como fallback quando `application_service` não está disponível
- Adicione queries customizadas de negócio abaixo conforme necessário
- O MetricsAgent executa todas as queries em paralelo durante o `/investigate`
