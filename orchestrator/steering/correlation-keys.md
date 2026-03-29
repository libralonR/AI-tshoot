# Regras de Correlação — Alertas (Grafana) ↔ Incidentes (PostgreSQL)

Escopo atual: apenas fontes já implementadas (Grafana MCP + Incidents PG MCP).
Métricas, logs e traces serão adicionados futuramente.

## Chave Principal de Correlação

`application_service` é a chave canônica que conecta alertas e incidentes.

- Nos alertas Grafana: label `application_service`
- Nos incidentes PostgreSQL: campo `cmdb_ci_name` (mesmo valor)

Sempre usar `application_service` como referência. Quando o dado vier do PostgreSQL
como `cmdb_ci_name`, normalizar para `application_service`.

## Hierarquia de Negócio (Grafana Labels)

```
business_capability          (mais amplo — identifica o time)
  └── business_domain
        └── business_service
              └── application_service   (mais específico — o componente)
```

- `business_capability`: Capacidade Tecnológica ou de Negócio. Identifica o time responsável.
- `business_domain`: Domínio dentro da capability.
- `business_service`: Serviço ou Produto de negócio.
- `application_service`: Componente técnico (API, rotina, serviço). Chave de correlação.

Regra: se souber o `application_service`, consegue derivar o time via `business_capability`.

## Labels de Ownership

| Label         | Descrição                                    | Presente em        |
|---------------|----------------------------------------------|--------------------|
| `owner_squad` | Equipe responsável (padrão ServiceNow/Jira)  | Grafana alerts     |
| `owner_sre`   | Equipe SRE responsável                       | Grafana alerts     |
| `assignment_group_name` | Grupo de atribuição do incidente   | Incidentes (PG)    |

Correlação: `owner_squad` (Grafana) == `assignment_group_name` (Incidentes).

## Labels Kubernetes (quando disponíveis)

Presentes em métricas, logs e traces (futuro). Nos alertas Grafana podem ou não estar presentes.

| Label       | Descrição                  |
|-------------|----------------------------|
| `namespace` | Namespace Kubernetes       |
| `pod`       | Nome do pod                |
| `cluster`   | Nome do cluster            |
| `env`       | Ambiente (production/staging) |

## Metadados Operacionais dos Alertas

| Label          | Descrição                                              |
|----------------|--------------------------------------------------------|
| `alertname`    | Nome da regra de alerta                                |
| `Severidade`   | P1, P2, P3                                             |
| `Datasource`   | Origem dos dados (VictoriaMetrics, Tempo, Zabbix)      |
| `grafana_folder` | Pasta/categoria do dashboard                        |
| `GIC`          | True = Service Desk faz primeiro atendimento (P2/Major)|
| `Ops24by7`     | True = gerenciado pelo time de Operações               |
| `SPoG`         | Classificação interna do time responsável              |
| `Teams`        | Canal no Microsoft Teams                               |
| `Fingerprint`  | Identificador único do alerta                          |

## Campos dos Incidentes (PostgreSQL)

Tabela: `public.incidents_snow`

| Campo                  | Uso na correlação                              |
|------------------------|------------------------------------------------|
| `number`               | Identificador do incidente (INC0012345)        |
| `cmdb_ci_name`         | = `application_service` (chave de correlação)  |
| `assignment_group_name`| = `owner_squad`                                |
| `priority`             | Correlaciona com `Severidade` do alerta        |
| `category`             | Categoria do incidente                         |
| `state`                | Estado atual (New, In Progress, Resolved...)   |
| `opened_at`            | Timestamp — define janela temporal             |
| `parent_incident`      | Permite rastrear incidentes filhos/relacionados|
| `short_description`    | Descrição curta — útil para contexto           |

## Normalização de Labels (Alias Mapping)

O orchestrator normaliza labels de diferentes fontes para um vocabulário canônico:

| Label original            | Fonte          | Label canônico         |
|---------------------------|----------------|------------------------|
| `application_service`     | Grafana        | `application_service`  |
| `cmdb_ci_name`            | Incidentes PG  | `application_service`  |
| `owner_squad`             | Grafana        | `owner_squad`          |
| `assignment_group_name`   | Incidentes PG  | `owner_squad`          |
| `Severidade`              | Grafana        | `severity`             |
| `priority`                | Incidentes PG  | `severity`             |

## Estratégia de Correlação

### Passo 1: Identificar application_service
- Se entrada é ALERT_UID → extrair `application_service` dos labels do alerta
- Se entrada é INCIDENT_ID → extrair `cmdb_ci_name` do incidente, normalizar para `application_service`
- Se entrada é SYMPTOM → tentar extrair nome de serviço do texto

### Passo 2: Buscar sinais cruzados
- Com `application_service` definido:
  - Buscar alertas firing no Grafana com mesmo `application_service`
  - Buscar incidentes no PostgreSQL com mesmo `cmdb_ci_name`
  - Buscar incidentes relacionados via `parent_incident`

### Passo 3: Enriquecer com contexto de negócio
- Extrair `business_capability` → identifica o time
- Extrair `owner_squad` / `owner_sre` → contato direto
- Extrair `Severidade` / `priority` → priorização

### Passo 4: Calcular confidence

| Condição                                              | Ajuste       |
|-------------------------------------------------------|--------------|
| Match por `application_service` em alerta + incidente | +0.25        |
| Match por `owner_squad` == `assignment_group_name`    | +0.10        |
| Match por `Severidade` == `priority`                  | +0.05        |
| Múltiplos alertas para mesmo `application_service`    | +0.15        |
| Incidentes relacionados via `parent_incident`         | +0.10        |
| Apenas match temporal (sem label em comum)            | -0.20        |
| `application_service` ausente                         | -0.30        |

## Gaps Conhecidos

- Alertas sem `application_service` preenchido → não correlacionam com incidentes
- Incidentes sem `cmdb_ci_name` → não correlacionam com alertas
- Labels de Kubernetes (`namespace`, `pod`, `cluster`) nem sempre presentes nos alertas
- `env`/`environment` não é label padrão nos alertas Grafana atuais
