# Grafana MCP Server - Kubernetes Deployment

Manifestos Kubernetes para rodar o Grafana MCP Server como um deployment escalável e seguro.

## Estrutura

```
k8s/grafana-mcp/
├── Dockerfile              # Container image
├── requirements.txt        # Python dependencies
├── .dockerignore          # Docker build exclusions
├── deployment.yaml        # Deployment manifest
├── service.yaml           # Service (ClusterIP)
├── serviceaccount.yaml    # ServiceAccount (least privilege)
├── configmap.yaml         # Configuration (non-sensitive)
├── secret.yaml            # Secrets (Grafana token)
├── networkpolicy.yaml     # Network policies (segurança)
├── hpa.yaml               # HorizontalPodAutoscaler
├── pdb.yaml               # PodDisruptionBudget
├── kustomization.yaml     # Kustomize config
└── README.md              # Este arquivo
```

## Pré-requisitos

1. **Cluster Kubernetes** (v1.24+)
2. **Namespace**: `observability` (ou ajuste nos manifestos)
3. **Grafana Token**: Token de API com permissões de leitura
4. **Container Registry**: Para armazenar a imagem Docker
5. **Metrics Server**: Para HPA funcionar (opcional)

## Build da Imagem

### 1. Copiar código fonte

```bash
# Copiar o servidor MCP para o diretório de build
cp ../../mcp-servers/grafana_v2.py k8s/grafana-mcp/grafana_v2.py
```

### 2. Build e push da imagem

```bash
cd k8s/grafana-mcp

# Build
docker build -t your-registry/grafana-mcp-server:v1.0.0 .

# Tag latest
docker tag your-registry/grafana-mcp-server:v1.0.0 your-registry/grafana-mcp-server:latest

# Push
docker push your-registry/grafana-mcp-server:v1.0.0
docker push your-registry/grafana-mcp-server:latest
```

### 3. Atualizar referência da imagem

Edite `deployment.yaml` e substitua:
```yaml
image: your-registry/grafana-mcp-server:latest
```

## Configuração

### 1. Criar namespace

```bash
kubectl create namespace observability
```

### 2. Configurar Grafana Token

**Opção A: Editar secret.yaml diretamente** (não recomendado para produção)

```bash
# Edite secret.yaml e substitua SUBSTITUA_COM_SEU_TOKEN_AQUI
vim secret.yaml
```

**Opção B: Criar secret via kubectl** (recomendado)

```bash
kubectl create secret generic grafana-mcp-secrets \
  --from-literal=grafana-token='seu_token_aqui' \
  --namespace observability \
  --dry-run=client -o yaml | kubectl apply -f -
```

**Opção C: Usar External Secrets Operator** (produção)

Veja exemplo comentado em `secret.yaml`.

### 3. Ajustar ConfigMap

Edite `configmap.yaml` para apontar para seu Grafana:

```yaml
data:
  grafana-url: "http://grafana.observability.svc.cluster.local:3000"
  # ou URL externa:
  # grafana-url: "https://grafana.example.com"
```

## Deploy

### Opção 1: Deploy direto com kubectl

```bash
kubectl apply -f serviceaccount.yaml
kubectl apply -f configmap.yaml
kubectl apply -f secret.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
kubectl apply -f networkpolicy.yaml
kubectl apply -f hpa.yaml
kubectl apply -f pdb.yaml
```

### Opção 2: Deploy com Kustomize (recomendado)

```bash
kubectl apply -k .
```

### Opção 3: Deploy com Helm (futuro)

```bash
# TODO: Criar Helm chart
helm install grafana-mcp-server ./helm-chart
```

## Verificação

### 1. Verificar pods

```bash
kubectl get pods -n observability -l app=grafana-mcp-server
```

Saída esperada:
```
NAME                                  READY   STATUS    RESTARTS   AGE
grafana-mcp-server-7d8f9c5b6d-abc12   1/1     Running   0          30s
grafana-mcp-server-7d8f9c5b6d-def34   1/1     Running   0          30s
```

### 2. Verificar logs

```bash
kubectl logs -n observability -l app=grafana-mcp-server --tail=50
```

### 3. Verificar service

```bash
kubectl get svc -n observability grafana-mcp-server
```

### 4. Testar conectividade

```bash
# Port-forward para teste local
kubectl port-forward -n observability svc/grafana-mcp-server 8080:8080

# Em outro terminal, teste (se o servidor expor HTTP)
curl http://localhost:8080/health
```

## Segurança

### Implementado

- ✅ **Non-root user**: Container roda como UID 1000
- ✅ **Read-only filesystem**: Root filesystem é read-only
- ✅ **No privilege escalation**: `allowPrivilegeEscalation: false`
- ✅ **Drop all capabilities**: Sem capabilities desnecessárias
- ✅ **Network policies**: Tráfego restrito (ingress/egress)
- ✅ **Resource limits**: CPU e memória limitados
- ✅ **ServiceAccount dedicado**: Sem token montado (não precisa de K8s API)
- ✅ **Secrets management**: Token em Secret (não hardcoded)

### Recomendações Adicionais

1. **Use External Secrets Operator** ou **Sealed Secrets** para gerenciar secrets
2. **Habilite Pod Security Standards** (restricted)
3. **Use Private Registry** com image pull secrets
4. **Scan de vulnerabilidades** na imagem (Trivy, Snyk)
5. **Rotate tokens** periodicamente
6. **Audit logs** para acesso ao MCP server

## Troubleshooting

### Pod não inicia

```bash
# Ver eventos
kubectl describe pod -n observability -l app=grafana-mcp-server

# Ver logs
kubectl logs -n observability -l app=grafana-mcp-server
```

Erros comuns:
- `Missing GRAFANA_URL or GRAFANA_TOKEN`: Secret ou ConfigMap não configurado
- `ImagePullBackOff`: Imagem não encontrada no registry
- `CrashLoopBackOff`: Erro no código ou configuração

### Erro de conexão com Grafana

```bash
# Testar conectividade do pod para Grafana
kubectl exec -it -n observability deployment/grafana-mcp-server -- \
  python -c "import httpx; print(httpx.get('http://grafana.observability.svc.cluster.local:3000/api/health').text)"
```

### HPA não escala

```bash
# Verificar metrics server
kubectl top nodes
kubectl top pods -n observability

# Ver status do HPA
kubectl get hpa -n observability grafana-mcp-server
kubectl describe hpa -n observability grafana-mcp-server
```

## Monitoramento

### Métricas (Prometheus)

O deployment está anotado para scraping do Prometheus:
```yaml
annotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "8080"
  prometheus.io/path: "/metrics"
```

**TODO**: Implementar endpoint `/metrics` no servidor MCP.

### Logs (Fluent Bit / Splunk)

Logs são enviados para stdout/stderr e coletados automaticamente pelo Fluent Bit.

Formato de log:
```
2026-03-05 23:14:45,817 - grafana-mcp - INFO - Tool called: find_firing_alerts with arguments: {}
```

### Alertas (Grafana)

Crie alertas para:
- Pod restarts > 3 em 5min
- CPU > 80% por 5min
- Memory > 90% por 5min
- Error rate > 5% por 1min

## Escalabilidade

### Horizontal (HPA)

- **Min replicas**: 2 (alta disponibilidade)
- **Max replicas**: 10
- **Trigger**: CPU > 70% ou Memory > 80%

### Vertical (VPA)

Para usar VPA em vez de HPA:

```bash
kubectl apply -f - <<EOF
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: grafana-mcp-server
  namespace: observability
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: grafana-mcp-server
  updatePolicy:
    updateMode: "Auto"
EOF
```

## Atualizações

### Rolling update

```bash
# Atualizar imagem
kubectl set image deployment/grafana-mcp-server \
  grafana-mcp=your-registry/grafana-mcp-server:v1.1.0 \
  -n observability

# Acompanhar rollout
kubectl rollout status deployment/grafana-mcp-server -n observability
```

### Rollback

```bash
# Ver histórico
kubectl rollout history deployment/grafana-mcp-server -n observability

# Rollback para versão anterior
kubectl rollout undo deployment/grafana-mcp-server -n observability

# Rollback para revisão específica
kubectl rollout undo deployment/grafana-mcp-server --to-revision=2 -n observability
```

## Limpeza

```bash
# Deletar todos os recursos
kubectl delete -k .

# Ou individualmente
kubectl delete deployment grafana-mcp-server -n observability
kubectl delete service grafana-mcp-server -n observability
kubectl delete configmap grafana-mcp-config -n observability
kubectl delete secret grafana-mcp-secrets -n observability
kubectl delete serviceaccount grafana-mcp-server -n observability
kubectl delete networkpolicy grafana-mcp-server -n observability
kubectl delete hpa grafana-mcp-server -n observability
kubectl delete pdb grafana-mcp-server -n observability
```

## Próximos Passos

1. [ ] Implementar endpoint `/health` e `/metrics`
2. [ ] Criar Helm chart
3. [ ] Adicionar ServiceMonitor (Prometheus Operator)
4. [ ] Implementar distributed tracing (OpenTelemetry)
5. [ ] Adicionar rate limiting
6. [ ] Criar dashboards Grafana para monitoramento
7. [ ] Documentar integração com Kiro/copilot
8. [ ] Adicionar testes de carga (k6)

## Referências

- [Kubernetes Best Practices](https://kubernetes.io/docs/concepts/configuration/overview/)
- [Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)
- [Network Policies](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
