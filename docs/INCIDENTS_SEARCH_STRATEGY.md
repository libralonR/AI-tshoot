# Estratégia de Busca de Incidentes

## Problema Identificado

O campo `cmdb_ci_name` na tabela `incidents_snow` **nem sempre está preenchido** e **pode diferir do `application_service` real** (ex: `cmdb_ci_name=Grafana` vs `application_service=grafana-tempo`). As informações do alerta Grafana (incluindo `application_service`) estão **sempre** no campo `description`.

## Solução Implementada

Todas as funções de busca agora **priorizam** o campo `description` e usam `cmdb_ci_name` apenas como fallback.

O orchestrator, ao receber um INCIDENT_ID, extrai `application_service` das `_grafana_labels` do description (prioridade) e usa `cmdb_ci_name` apenas como fallback para o scope.

### 1. `search_incidents` - Busca com Filtros

**Antes:**
```sql
WHERE i.cmdb_ci_name ILIKE '%rundeck-hom%'
```

**Depois (PRIORIDADE):**
```sql
WHERE (
    i.cmdb_ci_name ILIKE '%rundeck-hom%'
    OR i.description ILIKE '%- application_service=rundeck-hom%'
)
```

**Resultado:** Encontra incidentes mesmo quando `cmdb_ci_name` está vazio, buscando nas labels estruturadas do Grafana.

---

### 2. `get_related_incidents` - Busca por Application Service

**Estratégia:**

1. **PRIORIDADE:** Buscar no bloco `Labels:` do `description` (LIMIT 100)
   - Suporta múltiplas labels: `application_service`, `business_capability`, `business_domain`, `business_service`, `owner_squad`, `owner_sre`
   - Quando múltiplas labels são fornecidas, todas devem estar presentes (AND)
   ```sql
   WHERE i.description ILIKE '%- application_service=rundeck-hom%'
   AND i.description ILIKE '%- owner_squad=l-sre-observability%'
   AND i.opened_at >= NOW() - interval '24 hours'
   ```

2. **FALLBACK:** Buscar no `cmdb_ci_name` (LIMIT 50, somente se `application_service` foi fornecido)
   ```sql
   WHERE i.cmdb_ci_name ILIKE '%rundeck-hom%'
   AND i.opened_at >= NOW() - interval '24 hours'
   ```

3. **DEDUPLICAÇÃO:** Remove incidentes duplicados encontrados em ambas as buscas

**Exemplos de uso:**
```bash
# Por serviço
curl -X POST http://localhost:8082/tools/get_related_incidents \
  -d '{"arguments": {"application_service": "rundeck-hom"}}'

# Por capability
curl -X POST http://localhost:8082/tools/get_related_incidents \
  -d '{"arguments": {"business_capability": "corporate-services"}}'

# Por squad
curl -X POST http://localhost:8082/tools/get_related_incidents \
  -d '{"arguments": {"owner_squad": "l-sre-observability"}}'

# Combinado (AND)
curl -X POST http://localhost:8082/tools/get_related_incidents \
  -d '{"arguments": {"application_service": "rundeck-hom", "owner_squad": "l-sre-observability"}}'
```

**Logs:**
```
[get_related_incidents] Starting search | label_filters={'application_service': 'rundeck-hom', 'owner_squad': 'l-sre-observability'} | time_window=24h
[get_related_incidents] Priority search: Grafana labels in description field | conditions=2
[get_related_incidents] Found 8 incidents by Grafana labels (priority)
[get_related_incidents] Fallback search: cmdb_ci_name field
[get_related_incidents] Found 3 incidents by cmdb_ci_name (2 unique after dedup)
[get_related_incidents] Search completed | by_description=8 | by_ci=2 | total=10
```

---

### 3. `get_related_incidents` - Busca por Incident Number

**Estratégia:**

1. **Buscar incidente de referência** incluindo o campo `description`
   ```sql
   SELECT cmdb_ci_name, opened_at, sys_id, description
   FROM public.incidents_snow
   WHERE number = 'INC0012345'
   ```

2. **Extrair `application_service`** do `description` usando `parse_description()`
   ```python
   parsed = parse_description(description)
   grafana_labels = parsed.get("grafana_labels", {})
   ref_app_svc = grafana_labels.get("application_service")
   ```

3. **PRIORIDADE:** Buscar por `application_service` extraído do `description`
   ```sql
   WHERE i.description ILIKE '%- application_service=rundeck-hom%'
   AND i.opened_at BETWEEN ... AND ...
   ```

4. **FALLBACK:** Buscar por `cmdb_ci_name` **somente se** não encontrou nada no `description`
   ```sql
   WHERE i.cmdb_ci_name = 'rundeck-hom'
   AND i.opened_at BETWEEN ... AND ...
   ```

**Logs:**
```
[get_related_incidents] Reference incident found | cmdb_ci_name=None | application_service_from_description=rundeck-hom
[get_related_incidents] Priority search by application_service from description: rundeck-hom
[get_related_incidents] Found 8 incidents by application_service in description
```

---

## Padrões de Busca no Description

O campo `description` contém o corpo do alerta Grafana com labels no formato:

```
Labels:
- alertname=Utilização de Disco acima de 70%
- application_service=rundeck-hom
- business_capability=corporate-services
- business_domain=corporate-platform
- business_service=corporate-hub
- owner_squad=l-sre-observability
- owner_sre=l-sre-observability
- job=tempo-distributor
- k8s_cluster=eks-observability-01-use2-hom
```

### Padrões Buscados

1. **`- application_service=<valor>`** - Label estruturada do Grafana (busca exata)
2. Outras labels disponíveis para correlação:
   - `- business_capability=<valor>`
   - `- business_domain=<valor>`
   - `- business_service=<valor>`
   - `- owner_squad=<valor>`
   - `- owner_sre=<valor>`
   - `- job=<valor>`
   - `- k8s_cluster=<valor>`

**IMPORTANTE**: A busca é feita no bloco `Labels:` estruturado, não em campos livres como `CI:` que podem ter inconsistências.

### Função `parse_description()`

Extrai metadados estruturados do `description`:

```python
{
    "origin_url": "https://metrics.hom.corp/alerting/...",
    "panel_url": "...",
    "silence_url": "...",
    "alert_rule_uid": "cf53v5hkwgohse",
    "grafana_labels": {
        "alertname": "...",
        "application_service": "rundeck-hom",
        "instance": "rundeck-hom",
        "business_capability": "corporate-services",
        ...
    }
}
```

---

## Resultado Estruturado

Todas as buscas retornam:

```json
{
    "by_parent": [],       // Incidentes filhos/irmãos
    "by_ci": [],           // Por cmdb_ci_name (fallback)
    "by_description": []   // Por labels no description (PRIORIDADE)
}
```

---

## Benefícios

1. ✅ **Maior cobertura:** Encontra incidentes mesmo quando `cmdb_ci_name` está vazio
2. ✅ **Correlação precisa:** Usa as mesmas labels estruturadas que o Grafana envia
3. ✅ **Sem inconsistências:** Busca no bloco `Labels:` estruturado, não em campos livres como `CI:`
4. ✅ **Deduplicação automática:** Remove duplicatas entre as buscas
5. ✅ **Logs detalhados:** Rastreamento completo de cada busca
6. ✅ **Performance:** Busca prioritária retorna até 100 resultados, fallback até 50
7. ✅ **Múltiplas labels:** Suporte para `application_service`, `business_capability`, `business_domain`, `business_service`, `owner_squad`, `owner_sre`
8. ✅ **Filtros combinados (AND):** Múltiplas labels podem ser combinadas para busca mais precisa

---

## Exemplo de Uso

### Buscar incidentes relacionados a `rundeck-hom`:

```bash
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "SYMPTOM",
    "value": "alertas",
    "filters": {"application_service": "rundeck-hom"}
  }'
```

**Resultado esperado:**
- Busca alertas do Grafana com `application_service=rundeck-hom`
- Busca incidentes no bloco `Labels:` do `description` com `- application_service=rundeck-hom`
- Busca incidentes no `cmdb_ci_name` com `rundeck-hom` (fallback)
- Retorna todos os incidentes encontrados nas últimas 24h

---

## Monitoramento

Os logs mostram claramente a estratégia de busca:

```
[get_related_incidents] Starting search | label_filters={'application_service': 'rundeck-hom'} | time_window=24h
[get_related_incidents] Priority search: Grafana labels in description field | conditions=1
[get_related_incidents] Found 12 incidents by Grafana labels (priority)
[get_related_incidents] Fallback search: cmdb_ci_name field
[get_related_incidents] Found 5 incidents by cmdb_ci_name (3 unique after dedup)
[get_related_incidents] Search completed | by_description=12 | by_ci=3 | total=15
```

Busca com múltiplas labels:
```
[get_related_incidents] Starting search | label_filters={'business_capability': 'corporate-services', 'owner_squad': 'l-sre-observability'} | time_window=24h
[get_related_incidents] Priority search: Grafana labels in description field | conditions=2
[get_related_incidents] Found 5 incidents by Grafana labels (priority)
[get_related_incidents] Search completed | by_description=5 | by_ci=0 | total=5
```

Se `by_description=0` e `by_ci=0`, significa que:
- Não há incidentes com essas labels nas últimas 24h, OU
- O formato do bloco `Labels:` no `description` mudou e precisa ser ajustado
