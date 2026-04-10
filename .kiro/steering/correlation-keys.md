---
inclusion: manual
name: correlation-keys
description: Regras de correlação entre alertas (Grafana) e incidentes (PostgreSQL). Chave canônica: application_service.
---

# Correlation Keys

## Chave Canônica
`application_service` conecta alertas Grafana e incidentes PostgreSQL.

**IMPORTANTE**: No PostgreSQL, o campo `cmdb_ci_name` **nem sempre está preenchido**. As informações do alerta Grafana (incluindo `application_service`) estão **SEMPRE** no campo `description`.

### Estratégia de Busca

1. **PRIORIDADE**: Buscar no campo `description` por padrões:
   - `application_service=<valor>`
   - `instance=<valor>`
   - `CI:<valor>`
   - `Fingerprint: <valor>`

2. **FALLBACK**: Buscar no campo `cmdb_ci_name` (somente se necessário)

3. **NORMALIZAÇÃO**: Sempre normalizar para `application_service` canônico

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

**IMPORTANTE**: A busca de incidentes **prioriza** o campo `description` (sempre preenchido) sobre `cmdb_ci_name` (nem sempre preenchido).

1. Extrair `application_service` do alerta
2. Buscar incidentes no campo `description` por padrões:
   - `application_service=<valor>`
   - `instance=<valor>`
   - `CI:<valor>`
   - `Fingerprint: <valor>` (correlação precisa)
3. Fallback: Buscar no `cmdb_ci_name` se necessário
4. Enriquecer com `business_capability`, `owner_squad`, `owner_sre`
5. Calcular confidence baseado em matches

### Resultado Estruturado

```json
{
    "by_parent": [],       // Incidentes filhos/irmãos
    "by_ci": [],           // Por cmdb_ci_name (fallback)
    "by_description": []   // Por labels no description (PRIORIDADE)
}
```

## Gaps Conhecidos
- Alertas sem `application_service` → não correlacionam
- Incidentes sem `cmdb_ci_name` E sem labels no `description` → não correlacionam (raro, pois `description` é sempre preenchido)
- Labels K8s (`namespace`, `pod`, `cluster`) nem sempre presentes nos alertas

## Melhorias Implementadas
- ✅ Busca prioritária no campo `description` (sempre preenchido)
- ✅ Busca por múltiplos padrões: `application_service=`, `instance=`, `CI:`
- ✅ Fallback automático para `cmdb_ci_name`
- ✅ Deduplicação automática de resultados
- ✅ Parsing automático de labels do Grafana no `description`
