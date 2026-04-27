# Copilot UI — Kubernetes Deployment

Streamlit UI para o Observability Troubleshooting Copilot.

## Deploy

```bash
# Build e push
make build REGISTRY=your-registry TAG=v1.0.0
make push REGISTRY=your-registry TAG=v1.0.0

# Atualizar imagem no deployment.yaml
# Atualizar host no ingress.yaml

# Deploy
make deploy
# ou
kubectl apply -k .
```

## Configuração

| Variável | Descrição | Default |
|----------|-----------|---------|
| `ORCHESTRATOR_URL` | URL do orchestrator | `http://orchestrator.copilot.svc.cluster.local:8080` |

## Acesso

- Interno (ClusterIP): `http://copilot-ui.copilot.svc.cluster.local:8501`
- Externo (Ingress): Configurar host no `ingress.yaml`

## Verificação

```bash
kubectl -n copilot get pods -l app=copilot-ui
kubectl -n copilot logs -l app=copilot-ui -f
```
