# Estratégia de Busca de Incidentes

## Problema Identificado

O campo `cmdb_ci_name` na tabela `incidents_snow` **nem sempre está preenchido**. As informações do alerta Grafana (incluindo `application_service`) estão **sempre** no campo `description`.

## Solução Implementada

Todas as funções de busca agora **priorizam** o campo `description` e usam `cmdb_ci_name` apenas como fallback.

### 1. `search_incidents` - Busca com Filtros

**Antes:**
```sql
WHERE i.cmdb_ci_name ILIKE '%rundeck-hom%'
```

**Depois (PRIORIDADE):**
```sql
WHERE (
    i.cmdb_ci_name ILIKE '%rundeck-hom%'
    OR i.description ILIKE '%application_service=rundeck-hom%'
    OR i.description ILIKE '%instance=rundeck-hom%'
    OR i.description ILIKE '%CI:rundeck-hom%'
)
```

**Resultado:** Encontra incidentes mesmo quando `cmdb_ci_name` está vazio.

---

### 2. `get_related_incidents` - Busca por Application Service

**Estratégia:**

1. **PRIORIDADE:** Buscar no `description` (LIMIT 100)
   ```sql
   WHERE (
       i.description ILIKE '%application_service=rundeck-hom%'
       OR i.description ILIKE '%instance=rundeck-hom%'
       OR i.description ILIKE '%CI:rundeck-hom%'
   )
   AND i.opened_at >= NOW() - interval '24 hours'
   ```

2. **FALLBACK:** Buscar no `cmdb_ci_name` (LIMIT 50)
   ```sql
   WHERE i.cmdb_ci_name ILIKE '%rundeck-hom%'
   AND i.opened_at >= NOW() - interval '24 hours'
   ```

3. **DEDUPLICAÇÃO:** Remove incidentes duplicados encontrados em ambas as buscas

**Logs:**
```
[get_related_incidents] Priority search: description field for Grafana labels
[get_related_incidents] Found 12 incidents by description (priority)
[get_related_incidents] Fallback search: cmdb_ci_name field
[get_related_incidents] Found 5 incidents by cmdb_ci_name (3 unique after dedup)
[get_related_incidents] Search completed | by_description=12 | by_ci=3 | total=15
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
   WHERE (
       i.description ILIKE '%application_service=rundeck-hom%'
       OR i.description ILIKE '%instance=rundeck-hom%'
       OR i.description ILIKE '%CI:rundeck-hom%'
   )
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
- instance=rundeck-hom
- business_capability=corporate-services
- CI:rundeck-hom
```

### Padrões Buscados

1. **`application_service=<valor>`** - Label direto do Grafana
2. **`instance=<valor>`** - Label alternativo (muitas vezes igual ao application_service)
3. **`CI:<valor>`** - Formato do ServiceNow no description

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
2. ✅ **Correlação precisa:** Usa os mesmos labels que o Grafana envia
3. ✅ **Deduplicação automática:** Remove duplicatas entre as buscas
4. ✅ **Logs detalhados:** Rastreamento completo de cada busca
5. ✅ **Performance:** Busca prioritária retorna até 100 resultados, fallback até 50

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
- Busca incidentes no `description` com `application_service=rundeck-hom`
- Busca incidentes no `cmdb_ci_name` com `rundeck-hom` (fallback)
- Retorna todos os incidentes encontrados nas últimas 24h

---

## Monitoramento

Os logs mostram claramente a estratégia de busca:

```
[get_related_incidents] Priority search: description field for Grafana labels
[get_related_incidents] Found 12 incidents by description (priority)
[get_related_incidents] Fallback search: cmdb_ci_name field  
[get_related_incidents] Found 5 incidents by cmdb_ci_name (3 unique after dedup)
[get_related_incidents] Search completed | by_description=12 | by_ci=3 | total=15
```

Se `by_description=0` e `by_ci=0`, significa que:
- Não há incidentes com esse `application_service` nas últimas 24h, OU
- O formato do `description` mudou e precisa ser ajustado
