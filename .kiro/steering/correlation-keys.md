---
inclusion: manual
name: correlation-keys
description: Regras de correlação entre alertas (Grafana) e incidentes (PostgreSQL). Chave canônica: application_service.
---

# Correlation Keys

## Chave Canônica
`application_service` conecta alertas Grafana e incidentes PostgreSQL.
No PostgreSQL o campo equivalente é `cmdb_ci_name` — sempre normalizar para `application_service`.

## Hierarquia de Negócio (Grafana Labels)
```
business_capability → business_domain → business_service → application_service
```
`business_capability` identifica o time responsável.

## Alias Mapping

| Original                  | Fonte         | Canônico              |
|---------------------------|---------------|-----------------------|
| `application_service`     | Grafana       | `application_service` |
| `cmdb_ci_name`            | Incidentes PG | `application_service` |
| `owner_squad`             | Grafana       | `owner_squad`         |
| `assignment_group_name`   | Incidentes PG | `owner_squad`         |
| `Severidade`              | Grafana       | `severity`            |
| `priority`                | Incidentes PG | `severity`            |

## Correlação Alerta ↔ Incidente
1. Extrair `application_service` do alerta ou `cmdb_ci_name` do incidente
2. Buscar sinais cruzados com mesmo valor
3. Enriquecer com `business_capability`, `owner_squad`, `owner_sre`
4. Calcular confidence baseado em matches

## Gaps Conhecidos
- Alertas sem `application_service` → não correlacionam
- Incidentes sem `cmdb_ci_name` → não correlacionam
- Labels K8s (`namespace`, `pod`, `cluster`) nem sempre presentes nos alertas
