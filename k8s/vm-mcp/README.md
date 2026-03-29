# VictoriaMetrics MCP Server — Kubernetes Deployment

MCP server oficial da VictoriaMetrics para o Observability Troubleshooting Copilot.
Expõe APIs read-only do VictoriaMetrics via protocolo MCP (Streamable HTTP).

Repo oficial: https://github.com/VictoriaMetrics/mcp-victoriametrics

## Tools disponíveis

| Tool | Descrição | Default |
|------|-----------|---------|
| `query` | PromQL/MetricsQL instant query | ✅ |
| `query_range` | Range query com time period | ✅ |
| `metrics` | Listar métricas disponíveis | ✅ |
| `metrics_metadata` | Metadata (type, help, unit) | ✅ |
| `labels` | Listar label names | ✅ |
| `label_values` | Valores de um label específico | ✅ |
| `series` | Listar time series | ✅ |
| `rules` | Alerting e recording rules | ✅ |
| `alerts` | Alertas firing e pending | ✅ |
| `active_queries` | Queries em execução | ✅ |
| `top_queries` | Queries mais frequentes/lentas | ✅ |
| `tsdb_status` | Estatísticas de cardinalidade | ✅ |
| `tenants` | Listar tenants (cluster) | ✅ |
| `documentation` | Busca na documentação embutida | ✅ |
| `prettify_query` | Formatar PromQL/MetricsQL | ✅ |
| `explain_query` | Explicar como a query funciona | ✅ |

Tools desabilitadas por padrão (guardrail read-only):
`export`, `flags`, `metric_relabel_debug`, `downsampling_filters_debug`, `retention_filters_debug`, `test_rules`

## Pré-requisitos

1. Baixar o source code do repo oficial (sem acesso ao GitHub no build):
   ```bash
   # Na máquina com acesso ao GitHub
   git clone https://github.com/VictoriaMetrics/mcp-victoriametrics.git
   ```

2. Copiar o source para `k8s/vm-mcp/mcp-victoriametrics-source/`

## Build

```bash
cd k8s/vm-mcp

# Build (copia source, compila, gera imagem)
make build REGISTRY=rlibralon VM_MCP_SOURCE=./mcp-victoriametrics-source

# Push para registry
make push REGISTRY=rlibralon

# Limpar source do build context
make clean
```

## Configuração

Editar antes do deploy:

`configmap.yaml`:
```yaml
data:
  vm-instance-entrypoint: "http://victoriametrics.observability.svc.cluster.local:8428"
  vm-instance-type: "single"  # ou "cluster"
```

`secret.yaml` (se VictoriaMetrics exigir autenticação):
```yaml
stringData:
  vm-bearer-token: "seu-token"
```

## Deploy

```bash
# Aplicar todos os recursos
make deploy

# Verificar status
make status

# Ver logs
make logs
```

## Verificar

```bash
# Port-forward
kubectl port-forward -n observability svc/vm-mcp-server 8083:8080

# Health check
curl http://localhost:8083/health/liveness
curl http://localhost:8083/health/readiness

# Métricas Prometheus
curl http://localhost:8083/metrics

# UI (browser)
open http://localhost:8083/
```

## Teste local (Docker Compose)

```bash
# Na raiz do projeto
cp .env.example .env
# Editar VM_INSTANCE_ENTRYPOINT e VM_INSTANCE_TYPE no .env

docker compose up -d vm-mcp
curl http://localhost:8083/health/liveness
```

## Variáveis de ambiente

| Variável | Descrição | Obrigatório | Default |
|----------|-----------|-------------|---------|
| `VM_INSTANCE_ENTRYPOINT` | URL do VictoriaMetrics (vmsingle ou vmselect) | Sim | - |
| `VM_INSTANCE_TYPE` | `single` ou `cluster` | Sim | - |
| `VM_INSTANCE_BEARER_TOKEN` | Token de autenticação | Não | - |
| `MCP_SERVER_MODE` | `stdio`, `sse` ou `http` | Não | `stdio` |
| `MCP_LISTEN_ADDR` | Endereço de escuta | Não | `localhost:8080` |
| `MCP_DISABLED_TOOLS` | Tools desabilitadas (comma-separated) | Não | ver acima |

## Arquitetura

```
Orchestrator (copilot ns)
    │
    │ HTTP :8080
    ▼
VM MCP Server (observability ns)
    │
    │ HTTP (PromQL)
    ▼
VictoriaMetrics (vmsingle/vmselect)
```

## Recursos K8s

| Recurso | Arquivo |
|---------|---------|
| Deployment (2 replicas) | `deployment.yaml` |
| Service (ClusterIP) | `service.yaml` |
| ConfigMap | `configmap.yaml` |
| Secret | `secret.yaml` |
| ServiceAccount | `serviceaccount.yaml` |
| NetworkPolicy | `networkpolicy.yaml` |
| HPA (2-10 pods) | `hpa.yaml` |
| PDB (minAvailable: 1) | `pdb.yaml` |
| Kustomization | `kustomization.yaml` |

## NetworkPolicy

- Ingress: apenas do namespace `copilot` na porta 8080
- Egress: DNS (kube-system:53) + VictoriaMetrics (:8428)
