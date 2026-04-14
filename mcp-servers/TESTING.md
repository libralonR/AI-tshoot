# Testing Guide - MCP Servers

## Overview

Este guia documenta como testar os servidores MCP do Observability Troubleshooting Copilot.

## Servidores e Testes Disponíveis

| Servidor | Arquivo de Teste | Porta Default | Modo |
|----------|-----------------|---------------|------|
| Grafana MCP | `test_server_direct.py` | 8081 | Direto (call_tool) |
| Incidents PG MCP | `test_incidents_pg.py` | 8082 | REST + Direto |
| VM MCP Proxy | `test_vm_mcp_proxy.py` | 8084 | REST |

## Quick Start

```bash
# Grafana MCP (direto)
python mcp-servers/test_server_direct.py

# Incidents PG MCP (REST)
python mcp-servers/test_incidents_pg.py rest http://localhost:8082

# VM MCP Proxy (REST)
python mcp-servers/test_vm_mcp_proxy.py http://localhost:8084

# Todos com verbose
python mcp-servers/test_incidents_pg.py rest http://localhost:8082 --verbose
python mcp-servers/test_vm_mcp_proxy.py http://localhost:8084 --verbose
```

---

## VM MCP Proxy

### Pré-requisitos

1. VM MCP Server (Go binary) rodando em modo SSE ou HTTP
2. VM MCP Proxy (`vm_mcp_proxy.py`) rodando apontando para o upstream

```bash
# 1. Iniciar o VM MCP (Go binary)
export VM_INSTANCE_ENTRYPOINT=http://localhost:8428
export VM_INSTANCE_TYPE=single
export MCP_SERVER_MODE=sse
export MCP_LISTEN_ADDR=:8083
./mcp-victoriametrics

# 2. Iniciar o proxy
export VM_MCP_UPSTREAM=http://localhost:8083
export VM_MCP_MODE=sse
export PROXY_LISTEN_PORT=8084
python mcp-servers/vm_mcp_proxy.py

# 3. Executar testes
python mcp-servers/test_vm_mcp_proxy.py http://localhost:8084
```

### Testes (12 testes)

1. **Health Check** — Proxy up + upstream healthy
2. **List Tools** — Lista tools do VM MCP via proxy
3. **Instant Query (up)** — Query básica PromQL
4. **Count Series** — Query de contagem
5. **List Metrics** — Métricas disponíveis
6. **List Labels** — Labels disponíveis
7. **Label Values** — Valores de `__name__`
8. **TSDB Status** — Cardinalidade
9. **Alerts** — Alertas firing/pending
10. **Range Query** — Query com time range
11. **Documentation** — Busca na documentação
12. **Unknown Tool** — Erro gracioso

### Saída Esperada

```
======================================================================
  Testing VM MCP Proxy Adapter
======================================================================
Base URL: http://localhost:8084

▶ Test 1: Health Check
  ✓ PASS - status=ok | upstream_healthy=True

▶ Test 2: List Available Tools
  ✓ PASS - Found 18 tools

▶ Test 3: Instant Query (up)
  ✓ PASS - executionTime=0.045s

...

======================================================================
  Test Summary
======================================================================
Total Tests:  12
Passed:       12 ✓
Failed:       0 ✗
Success Rate: 100.0%

🎉 All tests passed!
```

---

## Incidents PG MCP

## Logs Detalhados por Função

### get_incident

```
[get_incident] Starting fetch for incident: INC0012345
[get_incident] Executing query for INC0012345
[get_incident] Query completed, result=found
[get_incident] Successfully fetched INC0012345 | cmdb_ci_name=aml-worker-service | priority=1 | state=In Progress | assignment_group=Squad Payments | grafana_labels_count=15 | alert_rule_uid=df4m8ngnj6br4e
```

### search_incidents

```
[search_incidents] Starting search with filters: {"application_service": "aml-worker", "priority": "1", "limit": 10}
[search_incidents] Filter: application_service LIKE '%aml-worker%'
[search_incidents] Filter: priority = 1
[search_incidents] Limit: 10 (requested: 10)
[search_incidents] Executing query with 2 conditions
[search_incidents] Query returned 7 rows
[search_incidents] Successfully returned 7 incidents | unique_services=3 | priority_distribution={'1': 7}
```

### get_related_incidents

```
[get_related_incidents] Starting search | number=INC0012345 | application_service=None | time_window=48h
[get_related_incidents] Fetching reference incident: INC0012345
[get_related_incidents] Reference incident found | cmdb_ci_name=aml-worker-service | opened_at=2026-04-06 10:00:00 | sys_id=abc123
[get_related_incidents] Searching for child/sibling incidents
[get_related_incidents] Found 2 incidents by parent relationship
[get_related_incidents] Searching for incidents with same CI: aml-worker-service
[get_related_incidents] Found 5 incidents by CI in time window
[get_related_incidents] Search completed | by_parent=2 | by_ci=5 | total=7
```

### get_incident_stats

```
[get_incident_stats] Starting stats calculation | application_service=aml-worker-service | days=30 | group_by=priority
[get_incident_stats] Filter: application_service LIKE '%aml-worker-service%'
[get_incident_stats] Executing stats query
[get_incident_stats] Query returned 3 groups
[get_incident_stats] Stats calculated | groups=3 | total_incidents=45 | period=30 days
[get_incident_stats] Top 1: 1 = 20 incidents
[get_incident_stats] Top 2: 2 = 15 incidents
[get_incident_stats] Top 3: 3 = 10 incidents
```

## Troubleshooting

### Incidents PG MCP

```bash
# Verificar se o servidor está rodando
curl http://localhost:8082/health

# Verificar variáveis de ambiente
echo $PG_USER $PG_HOST

# Testar conexão direta
psql -h $PG_HOST -U $PG_USER -d $PG_DATABASE
```

### VM MCP Proxy

```bash
# Verificar se o proxy está rodando
curl http://localhost:8084/health

# Verificar se o upstream (Go binary) está rodando
curl http://localhost:8083/health/liveness

# Verificar logs do proxy
# Procurar por: [MCPSSEClient] ou [MCPHTTPClient]

# Testar tool diretamente no upstream (se modo http)
curl -X POST http://localhost:8083/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/list"}'
```

### Grafana MCP

```bash
# Verificar se o servidor está rodando
curl http://localhost:8081/health

# Verificar variáveis de ambiente
echo $GRAFANA_URL $GRAFANA_TOKEN
```

## Integração com CI/CD

Todos os scripts retornam exit code apropriado:
- `0` se todos os testes passaram
- `1` se algum teste falhou

```bash
# Em pipeline CI/CD
python mcp-servers/test_incidents_pg.py rest http://incidents-pg-mcp:8082 || exit 1
python mcp-servers/test_vm_mcp_proxy.py http://vm-mcp-proxy:8084 || exit 1
```

## Métricas de Performance

Os logs incluem `executionTime` para cada operação:

- **get_incident**: ~0.05s (busca por índice)
- **search_incidents**: ~0.1-0.5s (depende dos filtros)
- **get_related_incidents**: ~0.2-0.8s (múltiplas queries)
- **get_incident_stats**: ~0.3-1.0s (agregação)

## Próximos Passos

1. Adicionar testes de carga (locust)
2. Adicionar testes de integração com orchestrator
3. Adicionar health check detalhado (verificar conexão PG / upstream VM MCP)
4. Adicionar cache para queries frequentes
