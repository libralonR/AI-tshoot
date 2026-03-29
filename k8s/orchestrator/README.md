# Orchestrator - Kubernetes Deployment

Manifestos Kubernetes para deploy do Orchestrator (cérebro do Observability Troubleshooting Copilot).

## Estrutura

```
k8s/orchestrator/
├── deployment.yaml        # Deployment com 2 replicas
├── service.yaml           # Service ClusterIP
├── serviceaccount.yaml    # ServiceAccount (least privilege)
├── configmap.yaml         # MCP server endpoints
├── secret.yaml            # LLM API keys (opcional)
├── networkpolicy.yaml     # Network policies
├── hpa.yaml               # HorizontalPodAutoscaler (2-10 pods)
├── pdb.yaml               # PodDisruptionBudget
├── ingress.yaml           # Ingress (NGINX)
├── kustomization.yaml     # Kustomize config
├── Makefile               # Comandos make
└── README.md              # Este arquivo
```

## Pré-requisitos

1. **Cluster Kubernetes** (v1.24+)
2. **Namespace**: `copilot` (criado automaticamente)
3. **MCP Servers**: Deployments dos MCP servers no namespace `observability`
4. **Container Registry**: Para armazenar a imagem Docker
5. **Metrics Server**: Para HPA funcionar (opcional)
6. **Ingress Controller**: NGINX Ingress Controller (opcional)

## Quick Start

### 1. Build e Push da Imagem

```bash
cd k8s/orchestrator

# Build
make build

# Push para registry
make push
```

### 2. Configurar

Edite `deployment.yaml` e atualize a imagem:
```yaml
image: your-registry/orchestrator:v1.0.0
```

Ou use Kustomize (edite `kustomization.yaml`):
```yaml
images:
- name: your-registry/orchestrator
  newTag: v1.0.0
```

### 3. Deploy

```bash
# Deploy completo
make all

# Ou passo a passo
make create-namespace
make deploy
make rollout-status
make status
```

## Configuração

### MCP Server Endpoints

Edite `configmap.yaml` para apontar para seus MCP servers:

```yaml
data:
  grafana-mcp-endpoint: "http://grafana-mcp-server.observability.svc.cluster.local:8080"
  vm-mcp-endpoint: "http://victoriametrics-mcp-server.observability.svc.cluster.local:8080"
  # ...
```

### LLM API Keys (Opcional)

Se você quiser usar LLMs para raciocínio avançado:

**Opção A: Via kubectl**
```bash
make create-secret
# Será solicitado interativamente
```

**Opção B: Editar secret.yaml**
```bash
vim secret.yaml
# Substitua SUBSTITUA_COM_SUA_CHAVE_OPENAI
kubectl apply -f secret.yaml
```

**Opção C: External Secrets Operator**
Veja exemplo comentado em `secret.yaml`.

### Ingress

Edite `ingress.yaml` para configurar seu domínio:

```yaml
spec:
  tls:
  - hosts:
    - copilot.example.com  # ← Seu domínio
    secretName: copilot-tls
  
  rules:
  - host: copilot.example.com  # ← Seu domínio
```

## Deploy

### Opção 1: Make (Recomendado)

```bash
# Deploy completo
make all

# Ou passo a passo
make build          # Build da imagem
make push           # Push para registry
make deploy         # Deploy no K8s
make rollout-status # Aguarda rollout
make status         # Mostra status
```

### Opção 2: Kustomize

```bash
kubectl apply -k .
kubectl rollout status deployment/orchestrator -n copilot
```

### Opção 3: kubectl direto

```bash
kubectl apply -f serviceaccount.yaml
kubectl apply -f configmap.yaml
kubectl apply -f secret.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
kubectl apply -f networkpolicy.yaml
kubectl apply -f hpa.yaml
kubectl apply -f pdb.yaml
kubectl apply -f ingress.yaml
```

## Verificação

### 1. Verificar pods

```bash
kubectl get pods -n copilot -l app=orchestrator

# Saída esperada:
# NAME                           READY   STATUS    RESTARTS   AGE
# orchestrator-7d8f9c5b6d-abc12  1/1     Running   0          1m
# orchestrator-7d8f9c5b6d-def34  1/1     Running   0          1m
```

### 2. Verificar logs

```bash
make logs

# Ou
kubectl logs -n copilot -l app=orchestrator --tail=50 -f
```

Logs esperados:
```
INFO:orchestrator:Starting Observability Troubleshooting Copilot Orchestrator
INFO:orchestrator:Loaded 3 steering files
INFO:orchestrator:MCP servers configured: ['grafana', 'victoriametrics', ...]
INFO:     Uvicorn running on http://0.0.0.0:8080
```

### 3. Verificar service

```bash
kubectl get svc -n copilot orchestrator-api

# Saída esperada:
# NAME              TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
# orchestrator-api  ClusterIP   10.96.123.456   <none>        8080/TCP   1m
```

### 4. Testar API

```bash
# Port-forward
make port-forward

# Em outro terminal
curl http://localhost:8080/health
curl http://localhost:8080/steering

# Investigar alerta
curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "input_type": "ALERT_UID",
    "value": "abc123def456",
    "user": "test@example.com"
  }'
```

## Monitoramento

### Métricas (Prometheus)

O deployment está anotado para scraping:
```yaml
annotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "8080"
  prometheus.io/path: "/metrics"
```

**TODO**: Implementar endpoint `/metrics` no orchestrator.

### Logs

Logs estruturados são enviados para stdout e coletados automaticamente.

### Alertas

Crie alertas para:
- Pod restarts > 3 em 5min
- CPU > 80% por 5min
- Memory > 90% por 5min
- Investigation errors > 10% por 1min

## Escalabilidade

### Horizontal (HPA)

- **Min replicas**: 2 (alta disponibilidade)
- **Max replicas**: 10
- **Trigger**: CPU > 70% ou Memory > 80%

```bash
# Ver status do HPA
kubectl get hpa -n copilot orchestrator

# Escalar manualmente
make scale REPLICAS=5
```

### Vertical (VPA)

Para usar VPA em vez de HPA, crie:

```yaml
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: orchestrator
  namespace: copilot
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: orchestrator
  updatePolicy:
    updateMode: "Auto"
```

## Segurança

### Implementado

- ✅ **Non-root user**: Container roda como UID 1000
- ✅ **Read-only filesystem**: Root filesystem é read-only
- ✅ **No privilege escalation**: `allowPrivilegeEscalation: false`
- ✅ **Drop all capabilities**: Sem capabilities desnecessárias
- ✅ **Network policies**: Tráfego restrito (ingress/egress)
- ✅ **Resource limits**: CPU e memória limitados
- ✅ **ServiceAccount dedicado**: Sem token montado
- ✅ **Secrets management**: API keys em Secret

### Recomendações Adicionais

1. **Use External Secrets Operator** para gerenciar secrets
2. **Habilite Pod Security Standards** (restricted)
3. **Use Private Registry** com image pull secrets
4. **Scan de vulnerabilidades** na imagem (Trivy, Snyk)
5. **Rotate API keys** periodicamente
6. **Audit logs** para acesso ao orchestrator

## Troubleshooting

### Pod não inicia

```bash
# Ver eventos
kubectl describe pod -n copilot -l app=orchestrator

# Ver logs
kubectl logs -n copilot -l app=orchestrator
```

**Erros comuns:**
- `ImagePullBackOff`: Imagem não encontrada → Verifique registry e tag
- `CrashLoopBackOff`: Erro no código → Verifique logs
- `Missing MCP endpoint`: ConfigMap não aplicado → `kubectl apply -f configmap.yaml`

### Erro de conexão com MCP servers

```bash
# Testar conectividade
kubectl exec -it -n copilot deployment/orchestrator -- \
  python -c "import httpx; print(httpx.get('http://grafana-mcp-server.observability.svc.cluster.local:8080/health').text)"
```

### HPA não escala

```bash
# Verificar metrics server
kubectl top nodes
kubectl top pods -n copilot

# Ver status do HPA
kubectl describe hpa -n copilot orchestrator
```

## Atualizações

### Rolling update

```bash
# Atualizar imagem
kubectl set image deployment/orchestrator \
  orchestrator=your-registry/orchestrator:v1.1.0 \
  -n copilot

# Acompanhar rollout
kubectl rollout status deployment/orchestrator -n copilot
```

### Rollback

```bash
# Ver histórico
kubectl rollout history deployment/orchestrator -n copilot

# Rollback
kubectl rollout undo deployment/orchestrator -n copilot

# Rollback para revisão específica
kubectl rollout undo deployment/orchestrator --to-revision=2 -n copilot
```

## Limpeza

```bash
# Deletar todos os recursos
make delete

# Ou individualmente
kubectl delete -k .

# Deletar namespace (cuidado!)
kubectl delete namespace copilot
```

## Comandos Úteis

```bash
make help              # Ver todos os comandos
make status            # Status do deployment
make logs              # Ver logs em tempo real
make port-forward      # Port-forward para teste local
make restart           # Restart do deployment
make scale REPLICAS=5  # Escalar manualmente
make delete            # Deletar tudo
make test-health       # Testar health endpoint
make test-steering     # Testar steering endpoint
```

## Próximos Passos

1. [ ] Implementar endpoint `/metrics` (Prometheus)
2. [ ] Adicionar ServiceMonitor (Prometheus Operator)
3. [ ] Implementar distributed tracing (OpenTelemetry)
4. [ ] Adicionar rate limiting no Ingress
5. [ ] Criar dashboards Grafana para monitoramento
6. [ ] Implementar CaseFile storage (PostgreSQL)
7. [ ] Adicionar testes de carga (k6)
8. [ ] Criar Helm chart

## Referências

- [Orchestrator README](../../orchestrator/README.md)
- [Architecture Flow](../../docs/ARCHITECTURE_FLOW.md)
- [K8s Architecture](../../docs/K8S_ARCHITECTURE.md)
- [Design Spec](../../.kiro/specs/observability-troubleshooting-copilot/design.md)
